"""
The hive, loose on the web - many bodies, one fixed dancer (worker 0).

Each worker gets its own Playwright page. Attendants roam continuously:
every tick, read the page, decide the next hop (hive.py: a visible
allowlisted link, else a seed), act. The dancer runs bout-gated instead:
decide the next hop, start a waggle bout pointed at its hashed angle
(dance.py), wait for the bout to finish, THEN commit that navigation - dance
first, fly second, the way a real forager finishes her waggle run before
leaving. None of that depends on a connectome: when build/graph.npz is
missing, the hive still roams and still dances, it just runs with
brain_online=False (hive.py runs the real LIF step purely for the
antennal-lobe display and mushroom-body bookkeeping when a graph IS loaded -
see that file's docstring). Nothing here moves a cursor pixel by pixel or
samples a screenshot - the dance is driven by links, not by FlyEye-style
vision; that upstream sense is deliberately not the main loop here.

RAILS, carried over from flycoinrh's roam.py:
* No wallet, no provider, no signing key anywhere in this process.
* No keyboard - a worker can click a link, nothing else.
* A click is checked before it lands: anything that reads like a submit, a
  file picker, a password field, a login, a payment or a wallet action is
  vetoed and logged as one. Also vetoed: any destination whose host isn't
  in the allowlist, or (github.com specifically) outside the comb repo's
  own path - github.com/about/*, /features/*, /solutions/* etc. are not a
  seed. A seed URL - or a same-page redirect/anchor variant of one, see
  SEED_PATHS - can never match either check.
* A hop budget: too many hops without landing anywhere new sends a worker
  back to the first seed.
* If a worker's page dies, that worker waits 6s and gets a fresh page at
  the first seed. The hive process does not go down with one body.
* COMB_LIVE does not exist in this file. There is no live-signing path here
  to gate.

Viewers subscribe over the websocket. There is no control channel back in.
"""
import argparse
import asyncio
import json
import math
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import _numpy_compat  # noqa: F401 — must run before scipy/flysim import; see that file
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from envcfg import load_env
from hive import build_hive, load_config, SEEDS

ROOT = Path(__file__).parent

# Checked against every URL a worker tries to commit to.
BLOCK = re.compile(
    r"(porn|xxx|adult|nsfw|escort|hentai|onlyfans|camsoda|chaturbate"
    r"|casino|bet365|poker|gambl|lottery"
    r"|checkout|/cart|/pay|payment|billing|invoice|subscribe"
    r"|/buy|/sell|/swap|/trade|connect-wallet|/deposit|/withdraw|wallet"
    r"|signin|sign-in|login|log-in|signup|sign-up|register|/auth"
    r"|password|passwd|account/delete|unsubscribe"
    r"|\.exe$|\.dmg$|\.msi$|\.apk$|\.zip$|\.torrent$|magnet:"
    r"|\.epub|\.mobi|\.pdf$|\.iso$|/download|kindle"
    r"|mailto:|tel:)", re.I)

# The fence: hosts of the four seeds in web/docs.html, nothing else. Open
# encyclopedia roam is off. Populated from comb.toml [allowlist].hosts once
# the hive's config is loaded; this fallback only matters if something
# imports allowed_host() before that happens.
ALLOW = {"en.wikipedia.org", "github.com", "dance.apis.online"}

# github.com is allowed only under the comb repo's own path - "the repo
# host", not the whole marketing site. Without this, the crawler drifts
# into github.com/about/*, /features/*, /solutions/*, /marketplace, none of
# which are a seed - those are vetoed the same as any other off-allow path.
SCOPED_PATHS = {"github.com": "/everestchris/comb-experiment"}

# Path prefixes that count as a seed even when the exact URL differs from
# the literal SEEDS entry (a redirect, an anchor, a query string) - e.g.
# "/wiki/Waggle_dance#Round_dance" is still the Waggle_dance seed. Checked
# against en.wikipedia.org only; the other two seeds (the repo, the live
# page) are matched by exact membership in SEEDS instead.
SEED_PATHS = ("/wiki/Waggle_dance", "/wiki/Apis_mellifera")

# Checked against a candidate link's text/label before a click lands:
# submit, file, password, buy, connect wallet, sign in, pay, download.
VETO = re.compile(
    r"(submit|upload|file|sign in|sign up|log in|log out|subscribe|buy|purchase"
    r"|sell|swap|trade|connect wallet|approve|confirm|bridge|stake"
    r"|checkout|pay |donate|delete|remove|report|flag|send|post|reply"
    r"|comment|password|credit card|download)", re.I)

LINKS_JS = """() => Array.from(document.querySelectorAll('a[href]'))
  .slice(0, 400)
  .map(a => ({href: a.href,
              text: (a.innerText || '').trim().slice(0, 80),
              label: a.getAttribute('aria-label') || ''}))"""

CLICK_JS = """(href) => {
  const as = Array.from(document.querySelectorAll('a[href]'));
  const el = as.find(a => a.href === href);
  if (!el) return false;
  el.scrollIntoView({block: 'center'});
  el.click();
  return true;
}"""

HOP_BUDGET = 40


def allowed_host(url):
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        if host not in ALLOW:
            return False
        prefix = SCOPED_PATHS.get(host)
        return prefix is None or p.path.startswith(prefix)
    except Exception:
        return False


def candidate_links(raw_links):
    """Hard-rail filtering only: on-allowlist, not blocklisted. No text-veto
    here - that check happens at click time, in may_click()."""
    out = []
    for L in raw_links:
        href = L.get("href") or ""
        if not href or BLOCK.search(href) or not allowed_host(href):
            continue
        out.append(href)
    return out


def is_seed(url):
    """True for an exact seed, or a URL whose host+path is still one of the
    two Wikipedia seeds under a redirect/anchor/query variant - see
    SEED_PATHS. Never matches by host alone."""
    if url in SEEDS:
        return True
    try:
        p = urlparse(url)
        if (p.hostname or "").lower() != "en.wikipedia.org":
            return False
        return any(p.path.startswith(sp) for sp in SEED_PATHS)
    except Exception:
        return False


def canonical_seed_for(url):
    """The exact SEEDS entry a seed-like `url` corresponds to, or None."""
    if url in SEEDS:
        return url
    try:
        p = urlparse(url)
        if (p.hostname or "").lower() != "en.wikipedia.org":
            return None
        for sp in SEED_PATHS:
            if p.path.startswith(sp):
                return f"https://en.wikipedia.org{sp}"
    except Exception:
        pass
    return None


def may_click(raw_links, href):
    if is_seed(href):
        return True, "ok (seed)"      # seed URLs can never match VETO or BLOCK
    if not allowed_host(href) or BLOCK.search(href):
        return False, "host not allowed"
    for L in raw_links:
        if L.get("href") == href:
            blob = f"{L.get('text','')} {L.get('label','')}"
            if VETO.search(blob):
                return False, f"veto: {blob.strip()[:40]}"
            return True, "ok"
    return False, "not on page"


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
                    allow_headers=["*"])
CLIENTS = set()
STATE = {"hive": None, "running": False, "port": 4663}


def say(*parts):
    try:
        print(*parts, flush=True)
    except Exception:
        pass


async def broadcast(msg):
    """Tell every watcher at once. A viewer is a subscriber, never a driver."""
    dead = []
    text = json.dumps(msg)
    for ws in list(CLIENTS):
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        CLIENTS.discard(ws)


def log(hive, msg):
    """One line to the console and into the hive's own rolling log, which
    rides along inside the next /ws tick (hive.snapshot()['log']) - there is
    no separate log channel to keep in sync with the state channel."""
    say("  " + str(msg))
    hive.add_log(msg)


async def goto_resilient(page, url, retries=0, timeout=20000, pause=1.2):
    """
    page.goto with optional retries - used for seed targets specifically.
    A trusted seed failing once (a transient timeout, a slow response) is
    not the same thing as a bad destination; a real Wikipedia article does
    not deserve a "blocked" verdict because Chromium was momentarily slow.
    Returns (status, ok_status) exactly like an inline goto would.
    """
    status, ok_status = 0, False
    for attempt in range(retries + 1):
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            status = resp.status if resp is not None else 200
            ok_status = status < 400
            if ok_status:
                return status, ok_status
        except Exception:
            status, ok_status = 0, False
        if attempt < retries:
            await asyncio.sleep(pause)
    return status, ok_status


async def commit_action(hive, worker, page, raw_links, action):
    """
    Actually perform a chosen Action - a click on a visible link, or a
    direct goto to a seed - and settle its outcome: reward/punish, trail
    reinforcement, and a log line. Shared by the dancer (called once a bout
    finishes) and every attendant (called every tick, immediately).
    """
    if action is None:
        return
    if action.kind == "navigate" and action.url:
        before = page.url
        seed_target = is_seed(action.url)
        status, ok_status = await goto_resilient(page, action.url, retries=2 if seed_target else 0)
        # Seed URLs are never vetoed and never classified "blocked" - they
        # are trusted by definition (docs.html's own Seeds list). Only a
        # real HTTP-level failure counts against one.
        if ok_status and (seed_target or (allowed_host(page.url) and not BLOCK.search(page.url))):
            hive.reward(worker)
            hive.trails.reinforce(page.url, worker.id)
            worker.url = page.url
            if seed_target:
                worker.hops_from_seed = 0   # hops reset on seed bounce
            log(hive, f"{status} {urlparse(page.url).path or '/'}")
        else:
            reason = "not_found" if not ok_status else "dead_end"
            hive.punish(worker, reason=reason)
            log(hive, f"{status or 'blocked'} {urlparse(action.url).path or '/'}")
            recovered_status, recovered_ok = await goto_resilient(
                page, before, retries=1 if is_seed(before) else 0, timeout=15000)
            if recovered_ok:
                worker.url = page.url
            else:
                await goto_resilient(page, SEEDS[0], retries=1)
                worker.url = page.url
                worker.hops_from_seed = 0   # hops reset on seed bounce

    elif action.kind == "click" and action.url:
        href = action.url
        ok, why = may_click(raw_links, href)
        if not ok:
            hive.punish(worker, reason="veto")
            log(hive, f"w{worker.id} did not click — {why}")
            return
        before = page.url
        try:
            clicked = await page.evaluate(CLICK_JS, href)
            if clicked:
                await page.wait_for_timeout(1200)
        except Exception:
            clicked = False
        if clicked and page.url != before:
            if is_seed(page.url) or (allowed_host(page.url) and not BLOCK.search(page.url)):
                hive.reward(worker)
                hive.trails.reinforce(page.url, worker.id)
                if is_seed(page.url):
                    worker.hops_from_seed = 0   # hops reset on seed bounce
                log(hive, f"200 {urlparse(page.url).path or '/'}")
            else:
                hive.punish(worker, reason="dead_end")
                log(hive, f"blocked {urlparse(page.url).path or '/'}")
                try:
                    await page.go_back(timeout=15000)
                except Exception:
                    pass
            worker.url = page.url


async def worker_body(hive, worker, browser, tick_s):
    """
    One worker's whole life: a Playwright page, a loop of look -> decide ->
    act, forever. A page dying does not end the hive - it ends this one
    coroutine's current context, rebuilt after 6s at the first seed.
    """
    while STATE["running"]:
        ctx = None
        try:
            ctx = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                accept_downloads=False,
                java_script_enabled=True,
            )
            page = await ctx.new_page()
            page.on("dialog", lambda d: asyncio.create_task(d.dismiss()))

            def _popup(p, _page=page):
                if p is not _page:
                    asyncio.create_task(p.close())
            ctx.on("page", _popup)

            await page.goto(worker.url, wait_until="domcontentloaded", timeout=30000)
            worker.url = page.url

            while STATE["running"]:
                t0 = time.monotonic()
                raw_links = []
                try:
                    raw_links = await page.evaluate(LINKS_JS)
                except Exception:
                    pass
                links = candidate_links(raw_links)

                hive.step_worker(worker)

                if worker.id == hive.dancer_id:
                    # Bout-gated: decide once, dance for the bout's full
                    # duration, only then commit the navigation.
                    dancing, _ = hive.dance_state()
                    if hive.current_bout is None:
                        action = hive.decide_next(worker, links=links)
                        bout = hive.begin_bout(worker, action)
                        deg = round(math.degrees(bout.angle_rad)) % 360
                        log(hive, f"w{worker.id} waggle {deg:.0f}° {bout.duration_s:.1f}s")
                    elif not dancing:
                        action = worker.pending_action
                        hive.current_bout = None
                        worker.pending_action = None
                        await commit_action(hive, worker, page, raw_links, action)
                    # else: mid-bout - nothing to do, the publisher already
                    # broadcasts the running bout every tick regardless.
                else:
                    action = hive.decide_next(worker, links=links)
                    await commit_action(hive, worker, page, raw_links, action)

                if worker.hops_from_seed >= HOP_BUDGET:
                    worker.hops_from_seed = 0
                    _, ok = await goto_resilient(page, SEEDS[0], retries=1)
                    if ok:
                        worker.url = page.url
                        log(hive, f"w{worker.id} hop budget spent — back to the seed")
                elif is_seed(worker.url) and worker.hops_from_seed > 12:
                    # Already sitting on a seed-like page (possibly a
                    # redirect/anchor variant) but hops never actually reset
                    # - re-home to the exact canonical seed and zero out.
                    canonical = canonical_seed_for(worker.url)
                    if canonical:
                        _, ok = await goto_resilient(page, canonical, retries=1)
                        if ok:
                            worker.url = page.url
                            worker.hops_from_seed = 0
                            log(hive, f"w{worker.id} re-homed to seed — hops reset")

                hive.end_tick()
                hive.maybe_save()

                elapsed = time.monotonic() - t0
                await asyncio.sleep(max(0.0, tick_s - elapsed))

            await ctx.close()
        except Exception as exc:
            say(f"worker {worker.id} died: {str(exc)[:120]} - respawning in 6s")
            try:
                if ctx is not None:
                    await ctx.close()
            except Exception:
                pass
            await asyncio.sleep(6)
            try:
                worker.url = SEEDS[0]
                worker.hops_from_seed = 0
                worker.pending_action = None
                if worker.id == hive.dancer_id:
                    hive.current_bout = None
            except Exception:
                pass


async def publisher(hive, hz=10.0):
    while STATE["running"]:
        await broadcast(hive.snapshot())
        await asyncio.sleep(1.0 / hz)


async def hive_main(headful=False):
    from playwright.async_api import async_playwright

    hive = build_hive()
    STATE["hive"] = hive
    ALLOW.clear()
    ALLOW.update(hive.cfg["allowlist"]["hosts"])
    say(f"hive ready: {len(hive.workers)} workers, brain_online={hive.brain_online}")

    tick_s = 1.0 / float(hive.cfg["hive"].get("tick_hz", 2.0))
    STATE["running"] = True

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not headful)
        tasks = [asyncio.create_task(worker_body(hive, w, browser, tick_s))
                 for w in hive.workers]
        tasks.append(asyncio.create_task(publisher(hive)))
        try:
            await asyncio.gather(*tasks)
        finally:
            STATE["running"] = False
            await browser.close()


@app.get("/")
def index():
    return FileResponse(str(ROOT / "web" / "index.html"))


@app.get("/docs")
def docs():
    return FileResponse(str(ROOT / "web" / "docs.html"))


@app.get("/api/state")
def state():
    hive = STATE["hive"]
    if hive is None:
        return {"ok": False, "graph": False, "workers": 0}
    return hive.snapshot()


@app.get("/api/health")
def health():
    hive = STATE["hive"]
    graph_ok = (ROOT / "build" / "graph.npz").exists()
    return {"ok": True, "graph": graph_ok, "workers": len(hive.workers) if hive else 0}


@app.websocket("/ws")
async def socket(ws: WebSocket):
    """A watcher. Viewers subscribe; they do not steer."""
    await ws.accept()
    CLIENTS.add(ws)
    try:
        while True:
            await ws.receive_text()   # read and discard - there is no command
    except Exception:
        pass
    finally:
        CLIENTS.discard(ws)


@app.on_event("startup")
async def begin():
    # Defence in depth for programmatic invocation (e.g. `uvicorn
    # roam_hive:app` directly, bypassing the __main__ guard below, which is
    # the one that actually exits 2). Here it just declines to open a
    # browser and leaves the static site serving.
    if load_env().get("FLY_ALLOW_BROWSER") != "1":
        say("FLY_ALLOW_BROWSER is not 1 - not opening a browser")
        return
    asyncio.create_task(hive_main(headful=STATE.get("headful", False)))


# Catch-all for static assets (css/js/data), mounted at "/" so index.html's
# own relative links ("css/comb.css", "js/bee.js", "data/bee.json") resolve
# correctly from the page's "/" URL. Registered last so it never shadows the
# explicit routes above.
app.mount("/", StaticFiles(directory=str(ROOT / "web")), name="web-assets")


if __name__ == "__main__":
    if load_env().get("FLY_ALLOW_BROWSER") != "1":
        say("FLY_ALLOW_BROWSER=1 is required to run roam_hive.py - "
            "this process opens a real (headless) Chromium browser and "
            "nothing runs without that being explicit. Refusing to start.")
        sys.exit(2)

    try:
        default_port = load_config()["hive"].get("port", 4663)
    except Exception:
        default_port = 4663
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=default_port)
    ap.add_argument("--host", default="127.0.0.1",
                     help="use 0.0.0.0 behind a platform proxy")
    ap.add_argument("--headful", action="store_true")
    args = ap.parse_args()
    STATE["port"] = args.port
    STATE["headful"] = args.headful
    say(f"the hive dances - listening on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
