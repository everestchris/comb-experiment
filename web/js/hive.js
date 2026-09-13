// web/js/hive.js — websocket subscription + HUD.
//
// This page is a viewer. It opens /ws and reads; it never sends a command
// back in (hard law 4 — viewers subscribe, they do not steer). The
// specimen itself (bee.js) never waits on this file or on /ws — it renders
// immediately from bee.json (or its own fallback) on load.
//
// Two independent badges: the socket pill (LIVE / CONNECTING) reflects
// only whether /ws is open; the brain pill (BRAIN ON / BRAIN OFFLINE)
// reflects only the latest tick's `brain` flag - a live socket says
// nothing about whether a connectome loaded, and this file never paints
// "brain on" without the backend having said so.
//
// Classic script, not a module — see waggle.js's header for why.

(function () {
  const $ = (id) => document.getElementById(id);

  function truncateUrl(url, max = 46) {
    if (!url) return "—";
    return url.length > max ? url.slice(0, max - 1) + "…" : url;
  }

  function buildAlBars(container, n) {
    container.innerHTML = "";
    const cells = [];
    for (let i = 0; i < n; i++) {
      const i_ = document.createElement("i");
      container.appendChild(i_);
      cells.push(i_);
    }
    return cells;
  }

  function initHud(beeScene) {
    const dancerUrl = $("hud-url");
    const dancerState = $("hud-state");
    const durFill = $("hud-dur-fill");
    const durVal = $("hud-dur-val");
    const vigorVal = $("hud-vigor-val");
    const compassNeedle = $("compass-needle");
    const polFill = $("hud-pol-fill");
    const polVal = $("hud-pol-val");
    const mbGrid = $("hud-mb");
    const workersList = $("hud-workers");
    const banner = $("hud-banner");
    const connPill = $("hud-conn");
    const brainPill = $("hud-brain");
    const brainStatus = $("hud-brain-status");
    const albarsEl = $("hud-albars");
    const logEl = $("hud-log");
    const overlayEl = $("specimen-overlay");

    const alCells = buildAlBars(albarsEl, 170);

    // A short human label for the overlay's "flower" line - the last path
    // segment, decoded and de-slugged ("Waggle_dance" -> "Waggle dance").
    // The right rail's own "flower url" field still shows the real,
    // unmodified page.url - this is a second, friendlier readout only.
    function flowerTitle(url) {
      try {
        const u = new URL(url);
        const seg = u.pathname.split("/").filter(Boolean).pop();
        if (!seg) return u.hostname || "—";
        return decodeURIComponent(seg).replace(/_/g, " ");
      } catch {
        return "—";
      }
    }

    // Registrable-ish domain for the overlay's "host" line (last two
    // dot-separated labels) - "en.wikipedia.org" -> "wikipedia.org".
    function shortHost(url) {
      try {
        const parts = new URL(url).hostname.split(".");
        return parts.length > 2 ? parts.slice(-2).join(".") : parts.join(".");
      } catch {
        return "—";
      }
    }

    const OV_LABEL_WIDTH = 10;
    function ovLine(label, value) {
      return label.padEnd(OV_LABEL_WIDTH) + value;
    }

    // One <pre> block, built as literal padded text - not flex/grid, so
    // column alignment never depends on layout quirks. The left overlay
    // binds the same state object as the right rail; no separate truth.
    function renderOverlay(s, d) {
      if (!overlayEl) return;
      const heading = `${Math.round(((d.angle || 0) * 180) / Math.PI)}°`;
      const lines = [
        ovLine("flower", flowerTitle(d.url).slice(0, 40)),
        ovLine("host", shortHost(d.url)),
        ovLine("heading", heading),
        ovLine("dist", `${(d.duration || 0).toFixed(2)}s`),
        ovLine("hops", String(d.hops ?? 0)),
      ];
      const tail = (s.log || []).slice(-4);
      if (tail.length) lines.push("", ...tail.map((l) => l.slice(0, 44)));
      overlayEl.textContent = lines.join("\n");
    }

    // s.log is a small rolling window of plain strings, resent whole on
    // every tick — no separate log channel to keep in sync with state, so
    // this just re-renders the list rather than diffing it.
    function renderLog(lines) {
      if (!logEl) return;
      logEl.innerHTML = "";
      for (const line of lines || []) {
        const d = document.createElement("div");
        if (/veto|did not click/.test(line)) d.className = "veto";
        else if (/^(blocked|0|4\d\d|5\d\d)\b/.test(line)) d.className = "blocked";
        d.textContent = line;
        logEl.appendChild(d);
      }
      logEl.scrollTop = logEl.scrollHeight;
    }

    // Two independent badges - a live socket says nothing about whether a
    // connectome loaded, and vice versa. Never optimistic: brain only ever
    // reads "brain on" when a tick has actually said s.brain === true.
    function setConn(ok) {
      connPill.classList.toggle("dancing", !!ok);
      connPill.querySelector(".label").textContent = ok ? "live" : "connecting…";
    }

    function setBrain(ok) {
      brainPill.classList.toggle("dancing", !!ok);
      brainPill.querySelector(".label").textContent = ok ? "brain on" : "brain offline";
      if (brainStatus) {
        brainStatus.textContent = ok ? "graph.npz loaded — AL/MB rates are real" : "no graph.npz — roam only";
      }
    }

    function applySnapshot(s) {
      if (!s || !s.dancer) return;
      setBrain(!!s.brain);
      renderLog(s.log);
      const d = s.dancer;
      renderOverlay(s, d);
      dancerUrl.textContent = truncateUrl(d.url);
      dancerUrl.title = d.url || "";
      dancerState.classList.toggle("dancing", !!d.dancing);
      dancerState.querySelector(".label").textContent = d.dancing ? "dancing" : "attending";

      const durPct = d.duration ? Math.min(100, (d.duration / 4.0) * 100) : 0;
      durFill.style.width = `${durPct}%`;
      durVal.textContent = `${(d.duration || 0).toFixed(2)}s`;
      vigorVal.textContent = (d.vigor || 0).toFixed(2);

      const deg = ((d.angle || 0) * 180) / Math.PI;
      compassNeedle.setAttribute("transform", `rotate(${deg} 44 44)`);

      const pol = s.pol || 0;
      polFill.style.width = `${pol * 100}%`;
      polVal.textContent = pol.toFixed(3);

      const al = s.al || [];
      for (let i = 0; i < alCells.length; i++) {
        alCells[i].style.opacity = String(0.06 + 0.94 * Math.min(1, al[i] || 0));
      }

      const mb = s.mb || {};
      mbGrid.innerHTML = "";
      const mbFields = [
        ["depressed", mb.depressed ?? "—"],
        ["mean gain", mb.mean_gain ?? "—"],
        ["rewards", mb.rewards ?? "—"],
        ["punishments", mb.punishments ?? "—"],
        ["veto", s.veto ?? 0],
      ];
      for (const [k, v] of mbFields) {
        const wrap = document.createElement("div");
        wrap.innerHTML = `<div class="k">${k}</div><div class="n">${v}</div>`;
        mbGrid.appendChild(wrap);
      }

      workersList.innerHTML = "";
      for (const w of s.workers || []) {
        const row = document.createElement("div");
        row.className = "wk" + (w.dancing ? " dancer" : "");
        row.innerHTML =
          `<span class="id">${w.id}</span>` +
          `<span class="role ${w.role}">${w.role}</span>` +
          `<span class="url">${truncateUrl(w.url, 34)}</span>`;
        workersList.appendChild(row);
      }

      if (banner && s.banner) banner.textContent = s.banner;

      if (beeScene) {
        beeScene.setDancerState({
          angle: d.angle || 0, duration: d.duration || 0,
          vigor: d.vigor || 0, dancing: !!d.dancing,
        });
        beeScene.setAttendantCount(Math.max(0, Math.min(7, (s.workers || []).length - 1)));
      }
    }

    function connectWs() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      let ws;
      try {
        ws = new WebSocket(`${proto}://${location.host}/ws`);
      } catch {
        setConn(false);
        setTimeout(connectWs, 3000);
        return;
      }
      ws.onopen = () => setConn(true);
      ws.onmessage = (ev) => {
        try { applySnapshot(JSON.parse(ev.data)); } catch { /* ignore malformed tick */ }
      };
      ws.onclose = () => { setConn(false); setBrain(false); setTimeout(connectWs, 3000); };
      ws.onerror = () => { try { ws.close(); } catch {} };
    }

    connectWs();
  }

  window.Comb = window.Comb || {};
  window.Comb.initHud = initHud;
})();
