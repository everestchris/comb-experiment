// web/js/bee.js — the 3D point-cloud specimen.
//
// Loads web/data/bee.json (built by scripts/make_bee.py from body
// primitives — see that file; this is a specimen plate, not a game asset).
// Black void, cool-white points, no ground plane, no textures, plus a thin
// hex-lattice backdrop standing in for the comb face itself. Orbit + idle
// auto-rotate. web/js/waggle.js drives the dance pose on top of this.
//
// The specimen must render with NO server and NO brain running: this file
// never awaits /ws or /api/health, and if fetching bee.json fails (which it
// always will when this page is opened as a bare file:// document, since
// fetch() of a local file is blocked by CORS there) it draws a procedural
// fallback point cloud built right here instead, so the pane is never
// empty. Classic script, not a module — see waggle.js's header for why.

(function () {
  const EYE_HEX = 0x7aa2c4;
  const BASE_HEX = 0xcfe7ff;

  const LAYERS_FALLBACK = ["head", "eye", "antenna", "thorax", "wing", "leg", "gaster", "sting"];

  // ---- deterministic small PRNG (mulberry32) — no dependency on numpy or
  // any server-side seed; this only ever runs client-side as a last resort.
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function gauss(rng) {
    let u = 0, v = 0;
    while (u === 0) u = rng();
    while (v === 0) v = rng();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  function ellipsoid(rng, out, layerId, center, radii, k, shell) {
    for (let i = 0; i < k; i++) {
      let x = gauss(rng), y = gauss(rng), z = gauss(rng);
      const d = Math.hypot(x, y, z) || 1;
      x /= d; y /= d; z /= d;
      const r = shell ? 0.86 + 0.14 * rng() : Math.cbrt(rng());
      out.push([
        center[0] + x * radii[0] * r,
        center[1] + y * radii[1] * r,
        center[2] + z * radii[2] * r,
        layerId,
      ]);
    }
  }

  // A compact stand-in bee — not the full make_bee.py silhouette, just
  // enough recognisable body-plan (head, eyes, thorax, wings, gaster) that
  // the pane is never blank when bee.json can't be fetched.
  function buildFallbackData(seed) {
    const rng = mulberry32(seed >>> 0);
    const pts = [];
    ellipsoid(rng, pts, 0, [0.38, 0, 0], [0.115, 0.115, 0.12], 2500, true);
    ellipsoid(rng, pts, 1, [0.40, 0.015, 0.09], [0.065, 0.07, 0.05], 2200, true);
    ellipsoid(rng, pts, 1, [0.40, 0.015, -0.09], [0.065, 0.07, 0.05], 2200, true);
    ellipsoid(rng, pts, 3, [0.13, 0, 0], [0.165, 0.135, 0.145], 5200, true);
    ellipsoid(rng, pts, 4, [-0.05, 0.10, 0.05], [0.34, 0.02, 0.14], 2400, false);
    ellipsoid(rng, pts, 4, [-0.05, 0.10, -0.05], [0.34, 0.02, 0.14], 2400, false);
    ellipsoid(rng, pts, 6, [-0.32, -0.01, 0], [0.26, 0.155, 0.155], 7200, true);
    ellipsoid(rng, pts, 5, [0.10, -0.25, 0.15], [0.03, 0.15, 0.03], 800, false);
    ellipsoid(rng, pts, 5, [0.10, -0.25, -0.15], [0.03, 0.15, 0.03], 800, false);

    const layers = {};
    LAYERS_FALLBACK.forEach((name, i) => {
      layers[String(i)] = {
        name,
        color: name === "eye" ? "#7aa2c4" : (name === "wing" ? "#a8c7dd" : "#cfe7ff"),
        count: pts.filter((p) => p[3] === i).length,
      };
    });
    return {
      points: pts,
      layers,
      meta: { species: "Apis mellifera", n_points: pts.length, seed,
               note: "procedural fallback — bee.json was not reachable" },
    };
  }

  async function loadBeeData(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`bee.json ${res.status}`);
    const data = await res.json();
    if (!data || !Array.isArray(data.points) || data.points.length < 100) {
      throw new Error("bee.json had no usable points");
    }
    return data;
  }

  function _makePoints(THREE, posArr, colArr, opacity) {
    if (!posArr.length) return null;
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(posArr), 3));
    geom.setAttribute("color", new THREE.BufferAttribute(new Float32Array(colArr), 3));
    const mat = new THREE.PointsMaterial({
      size: 0.016,
      vertexColors: true,
      sizeAttenuation: true,
      transparent: true,
      opacity,
      depthWrite: true,
    });
    return new THREE.Points(geom, mat);
  }

  function buildPoints(THREE, data, scale, opacity) {
    const rows = data.points;
    const eyeEntry = Object.entries(data.layers || {}).find(([, meta]) => meta.name === "eye");
    const eyeId = eyeEntry ? parseInt(eyeEntry[0], 10) : -1;
    const eyeColor = new THREE.Color(EYE_HEX);
    const baseColor = new THREE.Color(BASE_HEX);
    const pos = [], col = [];
    for (const row of rows) {
      pos.push(row[0] * scale, row[1] * scale, row[2] * scale);
      const c = row[3] === eyeId ? eyeColor : baseColor;
      col.push(c.r, c.g, c.b);
    }
    return _makePoints(THREE, pos, col, opacity ?? 0.92);
  }

  // Antenna base, in bee-local units (matches scripts/make_bee.py's antenna
  // attachment point) - the pivot the antenna sub-mesh rotates around.
  const ANTENNA_PIVOT = [0.44, 0.03, 0];

  // Splits one bee's points into a body cloud and an antenna-only cloud, the
  // latter re-centred on ANTENNA_PIVOT and positioned there, so rotating
  // just that Points object's .rotation.y wiggles the antennae around their
  // own base instead of the whole body's origin.
  function buildPointsSplit(THREE, data, scale, opacity) {
    const rows = data.points;
    const layers = data.layers || {};
    const antEntry = Object.entries(layers).find(([, m]) => m.name === "antenna");
    const antId = antEntry ? parseInt(antEntry[0], 10) : -1;
    const eyeEntry = Object.entries(layers).find(([, m]) => m.name === "eye");
    const eyeId = eyeEntry ? parseInt(eyeEntry[0], 10) : -1;
    const eyeColor = new THREE.Color(EYE_HEX), baseColor = new THREE.Color(BASE_HEX);
    const pivot = ANTENNA_PIVOT.map((v) => v * scale);

    const bodyPos = [], bodyCol = [], antPos = [], antCol = [];
    for (const row of rows) {
      const x = row[0] * scale, y = row[1] * scale, z = row[2] * scale;
      const c = row[3] === eyeId ? eyeColor : baseColor;
      if (row[3] === antId) {
        antPos.push(x - pivot[0], y - pivot[1], z - pivot[2]);
        antCol.push(c.r, c.g, c.b);
      } else {
        bodyPos.push(x, y, z);
        bodyCol.push(c.r, c.g, c.b);
      }
    }
    const body = _makePoints(THREE, bodyPos, bodyCol, opacity);
    const antenna = _makePoints(THREE, antPos, antCol, opacity);
    if (antenna) antenna.position.set(pivot[0], pivot[1], pivot[2]);
    return { body, antenna };
  }

  // ---- hex comb lattice: a thin-line honeycomb backdrop, not a ground
  // plane and not a measurement grid — it's the comb face the dance runs
  // happen on. Flat-top hexagons tiled on the vertical plane behind the
  // specimen, drawn as LineSegments so it stays faint and stays lines.
  function buildHexLattice(THREE, opts) {
    const { cols = 9, rows = 7, hexR = 0.22, z = -0.95, color = 0x2f4356 } = opts || {};
    const verts = [];
    const dx = 1.5 * hexR;
    const dy = Math.sqrt(3) * hexR;

    for (let c = -cols; c <= cols; c++) {
      const cx = c * dx;
      const colOffset = (c % 2 !== 0) ? dy / 2 : 0;
      for (let r = -rows; r <= rows; r++) {
        const cy = r * dy + colOffset;
        const hexVerts = [];
        for (let i = 0; i < 6; i++) {
          const a = (Math.PI / 3) * i;
          hexVerts.push([cx + hexR * Math.cos(a), cy + hexR * Math.sin(a)]);
        }
        for (let i = 0; i < 6; i++) {
          const p0 = hexVerts[i], p1 = hexVerts[(i + 1) % 6];
          verts.push(p0[0], p0[1], z, p1[0], p1[1], z);
        }
      }
    }

    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(verts), 3));
    const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.4 });
    return new THREE.LineSegments(geom, mat);
  }

  // The comb itself: a small, thin 3D slab behind the specimen - a real
  // volume with depth, not a flat wallpaper plane. opacity stays low
  // (~0.25) so it reads as "behind glass," not as the subject.
  function buildCombSlab(THREE, opts) {
    const { width = 3.0, height = 2.1, depth = 0.05, z = -0.97, color = 0x1c2b38 } = opts || {};
    const geom = new THREE.BoxGeometry(width, height, depth);
    const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.25 });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.position.z = z;
    return mesh;
  }

  class BeeScene {
    constructor(container) {
      const THREE = window.THREE;
      this.container = container;
      this.canvas = document.createElement("canvas");
      this.canvas.id = "specimen";
      container.appendChild(this.canvas);

      this.scene = new THREE.Scene();
      this.scene.background = new THREE.Color(0x000000);

      this.camera = new THREE.PerspectiveCamera(42, 1, 0.01, 100);
      this.camera.position.set(1.6, 0.9, 1.9);

      this.renderer = new THREE.WebGLRenderer({
        canvas: this.canvas, antialias: true, alpha: false,
      });
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

      this.controls = new window.OrbitControls(this.camera, this.canvas);
      this.controls.enableDamping = true;
      this.controls.dampingFactor = 0.06;
      this.controls.autoRotate = true;
      this.controls.autoRotateSpeed = 0.5;
      this.controls.minDistance = 0.6;
      this.controls.maxDistance = 6;
      this.controls.target.set(0, 0, 0);

      this.scene.add(buildCombSlab(THREE, {}));
      this.scene.add(buildHexLattice(THREE, {}));

      this.dancerGroup = new THREE.Group();
      this.scene.add(this.dancerGroup);
      this.attendantGroups = [];

      this._resize();
      window.addEventListener("resize", () => this._resize());
    }

    _resize() {
      const r = this.container.getBoundingClientRect();
      const w = Math.max(2, r.width | 0);
      const h = Math.max(2, r.height | 0);
      this.renderer.setSize(w, h, false);
      this.camera.aspect = w / Math.max(1, h);
      this.camera.updateProjectionMatrix();
    }

    async load(dataUrl) {
      const THREE = window.THREE;
      let data;
      try {
        data = await loadBeeData(dataUrl);
      } catch (err) {
        console.warn("bee.json unavailable, drawing procedural fallback specimen:", err.message);
        data = buildFallbackData(20260426);
      }
      this.data = data;

      // Dancer (w0) brighter than every attendant - opacity 0.92 vs 0.75.
      const dancer = buildPoints(THREE, data, 1.0, 0.92);
      this.dancerGroup.add(dancer);
      this.dancer = dancer;

      // Attendants w1..w7, arranged in a semicircle facing the dancer.
      // "never hold t=0": each gets its own phase offset (id*0.7) so their
      // idle motion (waggle.js) is never in lockstep.
      const nAttendants = 7;
      const radius = 1.4;
      for (let i = 0; i < nAttendants; i++) {
        const id = i + 1; // w1..w7
        const a = (i / nAttendants) * Math.PI - Math.PI / 2;
        const scale = 0.82 + (nAttendants > 1 ? (i / (nAttendants - 1)) * 0.06 : 0); // 0.82..0.88
        const g = new THREE.Group();
        const { body, antenna } = buildPointsSplit(THREE, data, scale, 0.75);
        if (body) g.add(body);
        if (antenna) {
          g.add(antenna);
          g.userData.antenna = antenna;
        }
        const restX = Math.cos(a) * radius, restZ = -Math.abs(Math.sin(a)) * radius - 0.6;
        g.position.set(restX, 0, restZ);
        g.userData.rest = { x: restX, y: 0, z: restZ };
        g.userData.id = id;
        g.userData.phase = id * 0.7;
        g.visible = false;
        this.scene.add(g);

        // A faint streak for the brief "recruit" walk (waggle.js) - a
        // world-space line from the attendant's rest spot to wherever it
        // currently is, invisible (opacity 0) except during that walk.
        const streakGeom = new THREE.BufferGeometry();
        streakGeom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
        const streakMat = new THREE.LineBasicMaterial({
          color: 0xcfe7ff, transparent: true, opacity: 0,
        });
        const streak = new THREE.Line(streakGeom, streakMat);
        streak.frustumCulled = false;
        streak.visible = false;
        this.scene.add(streak);
        g.userData.streak = streak;

        this.attendantGroups.push(g);
      }
      this.waggle = new window.Comb.Waggle(this.dancerGroup, this.attendantGroups);
      return data;
    }

    setAttendantCount(n) {
      const count = Math.max(0, Math.min(7, n));
      this.attendantGroups.forEach((g, i) => { g.visible = i < count; });
    }

    setDancerState(next) {
      if (this.waggle) this.waggle.setDancerState(next);
    }

    render() {
      this.controls.update();
      if (this.waggle) this.waggle.update(performance.now());
      this.renderer.render(this.scene, this.camera);
    }
  }

  async function initBee(container, dataUrl) {
    const scene = new BeeScene(container);
    await scene.load(dataUrl || "data/bee.json");
    const loop = () => {
      scene.render();
      requestAnimationFrame(loop);
    };
    loop();
    return scene;
  }

  window.Comb = window.Comb || {};
  window.Comb.BeeScene = BeeScene;
  window.Comb.initBee = initBee;
})();
