# comb-experiment

**Not a bee connectome.**

`comb-beta.0` is a behavioral costume — a hashed waggle-dance encoding —
wired onto a real, borrowed leaky-integrate-and-fire (LIF) simulation of the
published FlyEM male *Drosophila melanogaster* CNS connectome (v1.0). It is
released as an experiment in architecture, not as a model of honeybee
cognition: no public, complete *Apis mellifera* connectome exists, so none
is claimed or invented here. Where this document states a number, it is a
number taken from a run of this code, not an estimate.

Ticker `$COMB` (display only — no on-chain existence anywhere in this
codebase). Handle [@combexperiment](https://x.com/combexperiment).

## Abstract

A Playwright-driven crawler (`roam_hive.py`) roams a three-host allowlist.
One of eight workers is a fixed "dancer": it selects its next page, encodes
that page's URL into a heading via a frozen SHA-256 hash, holds a
duration/vigor-modulated waggle pose for the corresponding interval, and
only then executes the navigation — dance, then commit. The other seven
workers roam continuously and never dance. None of this depends on a
connectome load succeeding. When `build/graph.npz` is present, every worker
additionally runs a real LIF step over the loaded connectome each tick,
driving an antennal-lobe display and a dopamine-gated mushroom-body
plasticity rule — but the navigation and dance kinematics are unaffected
either way. This separation is the experiment's central design claim: a
real neural simulation and a scripted behavior can share a process without
the behavior secretly depending on (or being validated by) the simulation.

## What is real

- **The page.** `FLOWER URL` in the HUD is `page.url` on the live Playwright
  page at that instant — never a placeholder, never a cached string.
- **The connectome, when loaded.** `build/graph.npz` is the actual FlyEM
  male CNS v1.0 release: 165,122 traced neurons, 10,228,000 signed synapses
  (6,268,194 cholinergic/excitatory, 3,959,806 GABAergic-or-glutamatergic/
  inhibitory), built by `build_graph.py` with no inferred weights — synapses
  whose predicted neurotransmitter is a monoamine are zeroed, not guessed.
- **The one learning rule.** `mushroom.py`'s KC→MBON depression is the
  measured Drosophila rule (dopamine depresses, never potentiates, floor at
  0.25, slow recovery). It is the only place a weight moves in this process.
- **The antennal/polarization drive, when a graph is loaded.** Checked
  against the connectome at runtime, never assumed. Measured result on the
  v1.0 release: 2,641 neurons match the ORN/PN annotation pattern and 248
  match the R7d/R8d dorsal-rim pattern — real fly chemosensory and
  polarization-compass neurons, driven for real. (An earlier draft of this
  project's documentation assumed the reverse — that this graph carried
  neither — based on a prior release's structure, not a measurement of this
  one. That assumption was wrong and has been corrected in `antenna.py`.)
  Either way: real fly anatomy, never bee anatomy — see Anatomy split, below.
- **Veto, trails, hops, HTTP outcomes in the log.** `200 /wiki/...`,
  `blocked ...`, `w3 did not click — veto: ...` lines are the actual outcome
  of the actual navigation attempted that tick.
- **The recording.** `record.py` is a Playwright session recording this
  same localhost page, full-bleed, headless — no desktop, no cursor of
  anyone's, the same capture mechanics as `fruitflydev/flycoinrh`'s own
  `record.py`.

## What is costume

- **Waggle angle** = `sha256(next_url) % 360`, converted to radians
  (`dance.py`). A frozen function of the URL string alone. It is not read
  off the connectome and the connectome cannot influence it.
- **Waggle duration** = `clip(0.4 + 0.35 * hops_from_seed, 0.4, 4.0)`
  seconds. Hop count stands in for von Frisch's foraging-distance code.
- **Vigor** = `clip(0.3 + 0.5 * novelty, 0, 1)`, where novelty is this
  worker's own `1 / (1 + visits)` for the target page.
- **The point-cloud specimen and comb backdrop** (`web/`) — a body
  synthesized from primitives (`scripts/make_bee.py`), not a scan, not a
  download, not run through an image model.
- **Attendant idle motion** (yaw wobble, antenna wiggle, the periodic
  "recruit" walk) — client-side animation, cosmetic, never fed back into
  any decision.

## Anatomy split

| | |
|---|---|
| **Have** (real, cited) | Waggle-dance kinematics (von Frisch; Seeley 1995); the dorsal-rim polarization compass as a real sensory modality in bees (Rossel & Wehner 1984); *Apis* antennal-lobe glomerulus count on the order of 160–166 (Galizia lab atlas), used to size, not populate, a 170-channel scaffold |
| **Don't have** | Any public, complete *Apis mellifera* EM connectome, at any resolution |
| **Borrowed** | The FlyEM male CNS v1.0 LIF substrate and its one plasticity rule, unmodified from `fruitflydev/flycoinrh` |
| **Costume** | `$COMB`, the hex-lattice comb backdrop, the URL-hash dance mapping, worker roles, this HUD |

## Brain-optional operation

Roaming and dancing never depend on a connectome loading. On boot,
`hive.py`'s `resolve_graph_path()` checks, in order, and prints each result:

```
graph search: ./build/graph.npz            <FOUND|MISSING>
graph search: ../flycoinrh/build/graph.npz <FOUND|MISSING>
graph search: $FLY_GRAPH                   <FOUND|MISSING|unset>
BRAIN ON   (only if FlyBrain() actually loaded one)
BRAIN OFFLINE
```

`build/graph.npz` (~36MB, compressed) is committed to this repository, so a
fresh clone is BRAIN ON by default with no download. If it's ever missing,
the hive still starts, still roams, still dances — the HUD badge reads
BRAIN OFFLINE and the console prints the three FlyEM feather names and
where to get them (see `scripts/fetch_graph.md`) instead of crashing or
inventing a substitute.

## Rails

- No wallet, no provider, no signing key, anywhere in this process.
  `COMB_LIVE` is not read anywhere in the code — it exists in
  `.env.example` only so "off" is on the record.
- No keyboard. A worker can navigate or click an on-page link; nothing else.
- Every click is checked before it lands: submit/file/password/buy/connect-
  wallet/sign-in/pay/download language, or a host outside the three-host
  allowlist, is vetoed and logged. `github.com` is scoped to this repo's own
  path (`/everestchris/comb-experiment`) — `github.com/about/*`, `/features/*`,
  `/solutions/*` etc. are not a seed and are vetoed the same as anything else
  off-allow.
- Viewers subscribe over `/ws`. There is no control channel back in.
- `roam_hive.py` refuses to start (`exit 2`) unless `FLY_ALLOW_BROWSER=1` is
  set explicitly — this process opens a real headless Chromium and nothing
  runs without that being deliberate.

## Run book

```bash
git clone https://github.com/everestchris/comb-experiment
cd comb-experiment
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

cp .env.example .env
# edit .env: FLY_ALLOW_BROWSER=1

FLY_ALLOW_BROWSER=1 python roam_hive.py
# open http://localhost:4663  (BRAIN ON immediately - graph.npz is committed)
```

To record a clip of the live rig (same recorder lineage as flycoinrh's):

```bash
python record.py --port 4663
```

If you ever need to rebuild the connectome from scratch (`build/graph.npz`
missing or you want a different FlyEM release), see
`scripts/fetch_graph.md`.

To regenerate the point-cloud specimen (deterministic, numpy-only, no
connectome or browser required):

```bash
python scripts/make_bee.py
```

## Deploying

`render.yaml` + `Dockerfile` deploy this as one Docker web service on
Render, with `build/graph.npz` baked into the image (no persistent disk, no
1.1GB download at deploy time). See the comments in `render.yaml` for
sizing notes and the fallback download path if a fork ever strips the
committed graph back out.

## Status and known gaps

This is a running experiment, documented as it stands, not a finished
release:

- **Test suite: not yet written.** `tests/` exists but is currently empty.
  Planned coverage (frozen-hash determinism in `dance.py`, odor-channel
  hashing in `antenna.py`, dopamine floor/never-potentiate in `mushroom.py`)
  is designed to run without a browser or the 1.1GB connectome, matching
  the pattern in `fruitflydev/flycoinrh` and its ant-colony fork.
- **CI workflow: not yet written.** `.github/workflows/` exists but is
  empty. Planned: `make_bee.py` + a point-count assertion, then pytest, on
  every push.
- **`dance.apis.online`** is listed as a seed/allowed host but is not
  currently a deployed, resolvable site — navigations to it fail cleanly
  (logged, punished, worker respawns at the first seed) until it exists.

## Project layout

```
flysim.py, mushroom.py, envcfg.py, build_graph.py   kept from flycoinrh, unmodified
antenna.py        170-channel odor hash + polarization compass, brain-optional
dance.py          URL hash -> angle/duration/vigor - the whole dance mapping
hive.py           workers, shared brain (when loaded), trails, dopamine - no browser
roam_hive.py      Playwright + FastAPI/websocket driver - the only file allowed
                  to open a browser; boot-gated on FLY_ALLOW_BROWSER=1
record.py         headless-Chromium recorder for the live rig
scripts/make_bee.py   builds web/data/bee.json - a synthesized point cloud
scripts/fetch_graph.md   how to fetch/rebuild the connectome
comb.toml         every knob; nothing in it is anatomy
web/              THREE.js specimen + HUD, served by roam_hive.py
render.yaml, Dockerfile, render-entrypoint.sh   Render Blueprint deploy
```

## Credits

MIT license (see LICENSE) covers the code in this repository. The FlyEM
male CNS connectome is (c) HHMI Janelia FlyEM, the Cambridge Connectomics
Group and Google Research, CC-BY 4.0 — see NOTICE for the full attribution
chain, including what's borrowed from `fruitflydev/flycoinrh`.
