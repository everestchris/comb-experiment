"""
Record the live rig to a video file - not the desktop, not a screen
capture, just the rig's own page rendered full-bleed in a headless browser.

Opens http://localhost:4663 (or --port) in a Playwright browser that is
recording video, waits for the specimen to actually load, lets it run for
--timeout seconds, then closes the context so the video is finalised and
converts it to mp4 with ffmpeg if that's on PATH.

Carried over from fruitflydev/flycoinrh's record.py: the capture mechanics
(headless Chromium, record_video_dir, webm -> mp4), the "no desktop, no
taskbar, no cursor of yours in shot" guarantee, and a --dry refusal to
record a rig that looks armed to sign anything live. What's different: this
rig has no start button and no mint outcome to wait on - COMB_LIVE does not
exist anywhere in this codebase (see README, hard law 3) - the hive is
always-on, the same way flycoinrh's own roam.py was, so this just watches
whatever it is already doing and records for a fixed span.

The rig must already be running:  FLY_ALLOW_BROWSER=1 python roam_hive.py

  python record.py                 record whatever the hive is doing
  python record.py --dry           refuse if the rig looks armed to sign anything
  python record.py --port 4663     the hive's port (default)
"""
import argparse
import asyncio
import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "build" / "recordings"
UI = "http://localhost:4663"   # overridden by --port


def _get_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--timeout", type=int, default=60,
                     help="seconds of hive activity to capture")
    ap.add_argument("--dry", action="store_true",
                     help="abort if the rig looks armed to sign anything live")
    ap.add_argument("--port", type=int, default=4663,
                     help="port the hive listens on; roam_hive.py uses 4663")
    a = ap.parse_args()

    global UI
    UI = f"http://localhost:{a.port}"

    from playwright.async_api import async_playwright

    health = _get_json(f"{UI}/api/health")
    print(f"rig: graph={'loaded' if health.get('graph') else 'offline'} "
          f"workers={health.get('workers', 0)}")

    # There is no live-signing path anywhere in this codebase - no wallet, no
    # provider, no key, no pons launch (README, hard law 3). --dry checks
    # anyway, on the record, exactly the way flycoinrh's own --dry did: if
    # this rig's reported state ever claims to be live, refuse to record it.
    if a.dry and health.get("live"):
        raise SystemExit("rig reports live=true and --dry was given; aborting")

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": a.width, "height": a.height},
            record_video_dir=str(OUT),
            record_video_size={"width": a.width, "height": a.height},
        )
        page = await ctx.new_page()
        page.on("console", lambda m: m.type == "error"
                and print(f"  [ui.console] {m.text[:150]}"))

        await page.goto(UI, wait_until="domcontentloaded", timeout=45000)
        # Wait for the flower url to become a real page rather than the "—"
        # placeholder - the same condition the README's run book checks for
        # by eye before starting a recording. Brain-online or brain-offline,
        # the dancer's url updates on the very first /ws tick either way, so
        # this is a better readiness signal than the connection badge (whose
        # "dancing" state now reflects brain-online, not "connected").
        try:
            await page.wait_for_function(
                "() => { const el = document.getElementById('hud-url');"
                "        return el && el.textContent && el.textContent !== '—'; }",
                timeout=20000)
            print("first tick received - flower url is live")
        except Exception:
            print("no websocket tick yet - recording the idle specimen anyway")
        await page.wait_for_timeout(2000)
        print("rig loaded - recording ...")

        deadline = time.time() + a.timeout
        while time.time() < deadline:
            await page.wait_for_timeout(2000)
            try:
                url = await page.evaluate(
                    "() => (document.getElementById('hud-url')||{}).textContent || ''")
            except Exception:
                url = ""
            print(f"   ... {url[:70]}", end="\r", flush=True)
        print()

        try:
            logs = await page.evaluate(
                """() => { const el = document.getElementById('hud-log');
                           return el ? [...el.children].slice(-6).map(e => e.textContent) : []; }""")
        except Exception:
            logs = []
        for line in logs:
            print("  " + line.encode("ascii", "replace").decode())

        video = page.video
        await ctx.close()          # finalises the file
        await browser.close()
        raw = Path(await video.path())

    webm = OUT / f"comb-{stamp}.webm"
    raw.rename(webm)
    print(f"\nwrote {webm}  ({webm.stat().st_size / 1e6:.1f} MB)")

    if shutil.which("ffmpeg"):
        mp4 = OUT / f"comb-{stamp}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm),
                        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
                        "-pix_fmt", "yuv420p", str(mp4)], check=False)
        if mp4.exists():
            print(f"wrote {mp4}  ({mp4.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    asyncio.run(main())
