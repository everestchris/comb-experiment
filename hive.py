"""
The hive: N workers, one shared LIF body (when one is loaded), one shared
set of learned gains, one fixed dancer.

This file owns everything that can be reasoned about and tested without a
browser: config, the shared FlyBrain/MushroomBody/Antenna (when
build/graph.npz exists), per-worker odor state and eligibility traces, the
trail field, waggle bout timing, and dopamine delivery. It never imports
Playwright and never decides what a page's DOM looks like - that is
roam_hive.py's job.

Brain-optional by design: roaming and dancing never depend on the
connectome. The dance mapping (dance.py: URL hash -> angle, hop count ->
duration, novelty -> vigor) and the navigation policy below (a visible
allowlisted link, else a seed) are pure functions of what the browser sees
on the page. When build/graph.npz is present, every worker also runs a real
LIF step over it each tick - purely for the antennal-lobe display and the
mushroom body's dopamine bookkeeping - but nothing about where a worker
goes or how it dances is gated on that simulation succeeding. `brain_online`
tells the UI which mode it's in; it never gates the loop itself.

The dancer (worker 0) runs bout-gated: it decides its next page, starts a
waggle bout pointed at that page's hashed angle, and only actually navigates
once the bout finishes - dance, then commit, matching a real forager
finishing her waggle run before flying off. Attendants have no bout to wait
on; they just roam continuously.

Wiring law: the only weights that ever move are the KC->MBON gains inside
the shared MushroomBody instance (mushroom.py, untouched). Every worker
calls the same FlyBrain.run() over the same weight matrix; nothing here
adds a second place where a weight can change.

Worker 0 is always the dancer. There is no recruit-drive handoff in this
build - see README for why that's an honest simplification rather than a
missing feature: without a measured recruitment signal, rotating the
dancer would just be theatre.
"""
import collections
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import _numpy_compat  # noqa: F401 — must run before scipy/flysim import; see that file
import numpy as np

from antenna import Antenna, GLOMERULI
from dance import build_bout, hour_angle_rad
from envcfg import load_env
from flysim import FlyBrain
from mushroom import MushroomBody

ROOT = Path(__file__).parent
BUILD = ROOT / "build"

SEEDS = [
    "https://en.wikipedia.org/wiki/Waggle_dance",
    "https://en.wikipedia.org/wiki/Apis_mellifera",
    "https://github.com/everestchris/comb-experiment",
    "https://dance.apis.online",
]


def load_config(path=ROOT / "comb.toml"):
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with open(path, "rb") as f:
        return tomllib.load(f)


def _hash01(s, salt="comb-layout"):
    """Stable string -> [0,1) float. Used for layout/odor identity, never learning."""
    d = hashlib.sha256(f"{salt}:{s}".encode("utf-8")).digest()
    return int.from_bytes(d[:8], "big") / 2**64


# --------------------------------------------------------------------------
# trail field
# --------------------------------------------------------------------------
class TrailField:
    """
    A per-(host, url) trail-intensity reading, persisted as an append-only
    log at build/trails.jsonl.

    The in-memory table is the live intensities; the file is the ledger of
    every reinforce event, replayable to rebuild that table (last value per
    key wins) so a restarted hive does not start blind. A successful hop
    reinforces its page; that is the whole role trails play here - a record
    kept alongside the roam, not a signal read back into navigation.
    """

    def __init__(self, path, decay=0.985, reinforce=0.35, min_intensity=0.02):
        self.path = Path(path)
        self.decay = decay
        self.reinforce_amount = reinforce
        self.min_intensity = min_intensity
        self.table = {}   # (host, url) -> intensity in 0..1

    @staticmethod
    def key(url):
        return ((urlparse(url).hostname or "").lower(), url)

    def intensity(self, url):
        return self.table.get(self.key(url), 0.0)

    def decay_all(self):
        dead = []
        for k, v in self.table.items():
            v *= self.decay
            if v < self.min_intensity:
                dead.append(k)
            else:
                self.table[k] = v
        for k in dead:
            del self.table[k]

    def reinforce(self, url, worker, amount=None, ts=None):
        amount = self.reinforce_amount if amount is None else amount
        k = self.key(url)
        cur = self.table.get(k, 0.0)
        new = float(min(1.0, cur + amount * (1.0 - cur)))
        self.table[k] = new
        self._append(ts if ts is not None else time.time(), worker, url, new)
        return new

    def _append(self, ts, worker, url, intensity):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": ts, "worker": worker, "url": url,
               "intensity": round(float(intensity), 4)}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def load(self):
        """Replay the ledger. Malformed lines are skipped, not fatal."""
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                self.table[self.key(rec["url"])] = float(rec["intensity"])
            except Exception:
                continue


# --------------------------------------------------------------------------
# workers
# --------------------------------------------------------------------------
@dataclass
class Worker:
    id: int
    url: str
    role: str = "attendant"          # "dancer" | "attendant" — worker 0 only ever "dancer"
    veto_bias: float = 0.0
    hops_from_seed: int = 0
    visits: dict = field(default_factory=dict)
    trace: np.ndarray = None
    last_odors: dict = field(default_factory=dict)
    last_angle: float = 0.0
    pending_action: object = None    # dancer only: the Action chosen at bout-start,
                                      # executed once the bout finishes


@dataclass
class Action:
    kind: str            # "navigate" | "click" | "hold"
    url: str = None


# --------------------------------------------------------------------------
# hive
# --------------------------------------------------------------------------
class Hive:
    def __init__(self, fb, mb, antenna, trails, config, seeds=None, n_workers=None,
                 brain_online=False):
        self.fb = fb
        self.mb = mb
        self.antenna = antenna
        self.trails = trails
        self.cfg = config
        self.seeds = seeds or SEEDS
        self.brain_online = brain_online

        n = n_workers if n_workers is not None else config["hive"]["n_workers"]
        rng = random.Random(config.get("hive", {}).get("seed", 0) or None)
        n_pos = len(mb.pos) if mb is not None else 0
        self.workers = []
        for i in range(n):
            w = Worker(id=i, url=rng.choice(self.seeds), role="dancer" if i == 0 else "attendant")
            w.trace = np.zeros(n_pos, dtype=np.float32)
            self.workers.append(w)

        self.dancer_id = 0   # fixed - see module docstring
        self.current_bout = None
        self.bout_started = 0.0

        self.veto = 0
        self.hops = 0
        self.ticks = 0
        self._log = collections.deque(maxlen=40)

    def add_log(self, msg):
        self._log.append(str(msg)[:160])

    # -- odor vector --------------------------------------------------------
    def build_odors(self, worker, url, status_ok=True):
        """
        domain / path_tokens: a stable hashed smell-fingerprint of the URL's
        host and of the URL itself - not "newness" (that is `novelty`,
        below), just a fixed identity component so different pages present
        a different blend.

        novelty: 1 / (1 + times this worker has already been here) - starts
        at 1 for an unseen page and decays with repeat visits.

        status_ok: 1.0 if the last navigation to this URL came back
        HTTP-success, else 0.0 - odor intensity is a magnitude, so a failed
        hop smells of nothing rather than something bad.
        """
        host = urlparse(url).hostname or ""
        visits = worker.visits.get(url, 0)
        return {
            "domain": _hash01(host, salt="odor:domain"),
            "path_tokens": _hash01(url, salt="odor:path_tokens"),
            "novelty": float(np.clip(1.0 / (1.0 + visits), 0.0, 1.0)),
            "status_ok": 1.0 if status_ok else 0.0,
        }

    def novelty_of(self, worker, url):
        return float(np.clip(1.0 / (1.0 + worker.visits.get(url, 0)), 0.0, 1.0))

    # -- one worker's brain step ---------------------------------------------
    def step_worker(self, worker, status_ok=True, seed=None):
        """
        Runs the real LIF simulation when a connectome is loaded, purely to
        populate the antennal-lobe display and let the mushroom body's
        dopamine bookkeeping keep moving. Always records a visit regardless
        of brain_online - novelty (and therefore vigor and the odor vector)
        must keep working with the brain offline.
        """
        worker.visits[worker.url] = worker.visits.get(worker.url, 0) + 1

        if not self.brain_online:
            return worker.role, {}

        odors = self.build_odors(worker, worker.url, status_ok)
        worker.last_odors = odors
        drive = self.antenna.smell(odors)

        pol_drive = self.antenna.polarize(worker.last_angle, hour_angle_rad())
        for k, v in pol_drive.items():
            drive[k] = max(drive.get(k, 0.0), v)

        r = self.fb.run(drive, steps=self.cfg["hive"].get("brain_steps", 60),
                         gains=None, seed=seed if seed is not None else worker.id + self.ticks)

        # The one shared circuit that is allowed to learn. Swap in this
        # worker's own eligibility trace, run the observe/forget/apply cycle
        # every tick, then keep the trace on the worker - the gains stay on
        # the shared MushroomBody, the one thing common to the whole hive.
        self.mb.trace = worker.trace
        self.mb.observe(r.get("_fired"))
        self.mb.forget()
        self.mb.apply()
        worker.trace = self.mb.trace
        return worker.role, {}

    # -- dance bout management -----------------------------------------------
    def dance_state(self, now=None):
        """(dancing: bool, elapsed_s: float) for the current bout, if any."""
        if self.current_bout is None:
            return False, 0.0
        now = now if now is not None else time.time()
        elapsed = now - self.bout_started
        return elapsed < self.current_bout.duration_s, elapsed

    def begin_bout(self, worker, target_action, now=None):
        """
        Start a fresh waggle bout pointed at `target_action.url` - the page
        this worker is about to visit next, not the one it is currently on
        (that stays "flower" in the UI; see dancer_view()).

        vigor = clip(0.3 + 0.5 * novelty, 0, 1) - a target this worker has
        barely seen dances harder than one it has circled back to many
        times. Nothing here reads price, a trade, or a wallet.
        """
        target_url = target_action.url
        novelty = self.novelty_of(worker, target_url)
        vigor_signal = 0.3 + 0.5 * novelty
        bout = build_bout(target_url, worker.hops_from_seed, vigor_signal)
        worker.last_angle = bout.angle_rad
        worker.pending_action = target_action
        self.current_bout = bout
        self.bout_started = now if now is not None else time.time()
        return bout

    # -- navigation policy ---------------------------------------------------
    def _hash_pick(self, worker, links):
        """Deterministic-per-tick pick among candidate links - a fixed hash
        of (worker, tick, link), not a claim of anything cognitive."""
        def h(u):
            d = hashlib.sha256(f"{worker.id}:{self.ticks}:{u}".encode("utf-8")).digest()
            return int.from_bytes(d[:8], "big")
        return min(links, key=h)

    def decide_next(self, worker, links=None):
        """
        links: allowlisted candidate hrefs on the worker's current page,
        already filtered by the driver - hive.py holds no DOM/allowlist
        logic of its own.

        next = a visible allowlisted link (hash-picked), else a seed. A
        link is subject to the driver's own text-based veto check before a
        click lands (roam_hive.py's may_click()), which is the only veto
        gate; this file never blocks a click on its own.
        """
        links = links or []
        if links:
            return Action("click", url=self._hash_pick(worker, links))
        return Action("navigate", url=random.choice(self.seeds))

    # -- dopamine, delivered once an action's outcome is known ---------------
    def reward(self, worker, amount=None):
        worker.hops_from_seed += 1
        self.hops += 1
        if not self.mb:
            return
        amount = self.cfg["dopamine"]["reward"] if amount is None else amount
        self.mb.trace = worker.trace
        self.mb.dopamine(+1, amount)
        self.mb.apply()
        worker.trace = self.mb.trace

    def punish(self, worker, reason="dead_end", amount=None):
        if reason == "veto":
            worker.veto_bias = float(min(1.0, worker.veto_bias + 0.15))
            self.veto += 1
        if not self.mb:
            return
        key = {"veto": "veto_penalty", "not_found": "not_found_penalty",
               "dead_end": "dead_end_penalty"}.get(reason, "dead_end_penalty")
        amount = self.cfg["dopamine"].get(key, 1.0) if amount is None else amount
        self.mb.trace = worker.trace
        self.mb.dopamine(-1, amount)
        self.mb.apply()
        worker.trace = self.mb.trace

    # -- upkeep ---------------------------------------------------------------
    def end_tick(self):
        self.trails.decay_all()
        self.ticks += 1

    def maybe_save(self, every=40):
        if self.mb and self.ticks % every == 0:
            self.mb.save()

    # -- for the UI -------------------------------------------------------
    def worker_view(self, worker):
        return {"id": worker.id, "role": worker.role, "url": worker.url}

    def dancer_view(self):
        dancer = self.workers[self.dancer_id]
        dancing, _ = self.dance_state()
        bout = self.current_bout
        return {
            "id": dancer.id,
            "url": dancer.url,
            "angle": round(bout.angle_rad, 4) if bout else 0.0,
            "duration": round(bout.duration_s, 3) if bout else 0.0,
            "vigor": round(bout.vigor, 4) if bout else 0.0,
            "hops": dancer.hops_from_seed,
            "dancing": bool(dancing),
        }

    def snapshot(self):
        dancer = self.dancer_view()
        mb_stats = self.mb.stats() if self.mb else {}
        return {
            "live": True,
            "brain": self.brain_online,
            "dancer": dancer,
            "flower": dancer["url"],
            "workers": [self.worker_view(w) for w in self.workers],
            "al": self.antenna.al_bar() if self.antenna else [0.0] * GLOMERULI,
            "pol": self.antenna.pol_reading() if self.antenna else 0.0,
            "mb": {
                "mean_gain": mb_stats.get("mean_gain", 1.0),
                "depressed": mb_stats.get("depressed", 0),
                "rewards": mb_stats.get("rewards", 0),
                "punishments": mb_stats.get("punishments", 0),
            },
            "veto": self.veto,
            "hops": self.hops,
            "log": list(self._log),
        }


FEATHERS = (
    "connectome-weights-male-cns-v1.0-minconf-0.5.feather",
    "body-neurotransmitters-male-cns-v1.0.feather",
    "body-annotations-male-cns-v1.0-minconf-0.5.feather",
)


def resolve_graph_path(verbose=True):
    """
    Where build/graph.npz might actually be, checked and printed in this
    order:

      1. ./build/graph.npz            - this repo's own build directory
      2. ../flycoinrh/build/graph.npz - a sibling clone of the upstream fork,
         so a machine that already built the fly's connectome once does not
         have to rebuild or re-download it for the bee
      3. $FLY_GRAPH                   - an explicit path override

    Prints one "graph search: <label>  <status>" line per candidate as it
    checks, then stops at the first one that exists. Returns that path, or
    None if all three are missing. Never downloads, never invents a graph -
    see scripts/fetch_graph.md for how to get a real one.
    """
    env_raw = load_env().get("FLY_GRAPH")
    candidates = [
        ("./build/graph.npz", BUILD / "graph.npz"),
        ("../flycoinrh/build/graph.npz", ROOT.parent / "flycoinrh" / "build" / "graph.npz"),
        ("$FLY_GRAPH", Path(env_raw) if env_raw else None),
    ]
    for label, path in candidates:
        if path is None:
            if verbose:
                print(f"graph search: {label:<28} unset")
            continue
        if path.exists():
            if verbose:
                print(f"graph search: {label:<28} FOUND")
            return path
        if verbose:
            print(f"graph search: {label:<28} MISSING")
    return None


def build_hive(config_path=ROOT / "comb.toml", seeds=None):
    """
    Load config and stand up the hive. Never invents connectome weights: if
    no graph.npz is found (or the one found fails to load), the hive still
    comes up and still roams and dances - see module docstring - it just
    runs with brain_online=False instead of refusing to start, and never
    reports BRAIN ON unless FlyBrain() actually succeeded.
    """
    cfg = load_config(config_path)
    graph_path = resolve_graph_path()
    fb = mb = antenna = None
    brain_online = False

    if graph_path is not None:
        try:
            fb = FlyBrain(graph_path)
            mb = MushroomBody(fb)
            a_cfg = cfg["antenna"]
            antenna = Antenna(fb, salt=a_cfg["salt"], glomeruli=a_cfg.get("glomeruli", GLOMERULI),
                               per_odor=a_cfg.get("glomeruli_per_odor", 8),
                               max_hz=a_cfg.get("max_hz", 180.0))
            brain_online = True
        except Exception as exc:
            print(f"FlyBrain() failed on {graph_path} ({str(exc)[:160]})")
            fb = mb = antenna = None

    print("BRAIN ON" if brain_online else "BRAIN OFFLINE")
    if not brain_online:
        print("To go ON, the operator must fetch the three FlyEM male-CNS v1.0 feathers "
              "from storage.googleapis.com/flyem-male-cns (CC-BY, no account needed) into "
              "data/, then run: python build_graph.py — see scripts/fetch_graph.md")
        for name in FEATHERS:
            print(f"  {name}")

    t_cfg = cfg["trails"]
    trails = TrailField(ROOT / t_cfg["path"], decay=t_cfg["decay"],
                         reinforce=t_cfg["reinforce"], min_intensity=t_cfg["min_intensity"])
    trails.load()

    n_workers = None
    raw = load_env().get("COMB_WORKERS")
    if raw:
        try:
            n_workers = int(raw)
        except ValueError:
            pass

    hive = Hive(fb, mb, antenna, trails, cfg, seeds=seeds, n_workers=n_workers,
                brain_online=brain_online)
    if brain_online and antenna is not None and not antenna.anatomical:
        msg = "graph loaded, AL still hashed — no bee glomeruli in this matrix"
        print(msg)
        hive.add_log(msg)
    return hive


if __name__ == "__main__":
    hive = build_hive()
    dancer = hive.workers[0]
    hive.begin_bout(dancer, Action("navigate", url=hive.seeds[0]))
    print(f"hive ready: {len(hive.workers)} workers, brain_online={hive.brain_online}")
    for w in hive.workers:
        hive.step_worker(w)
        print(f"  worker {w.id}: {w.role} @ {w.url}")
