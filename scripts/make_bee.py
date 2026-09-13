"""
Builds web/data/bee.json - a point-cloud worker honeybee, synthesized from
body primitives (gaussian-shell ellipsoids and bent point-lines), the same
family of technique fruitflydev/flycoinrh used for its own particle-scan fly
plate. Nothing here is downloaded, traced from an image, or run through an
image model - see README hard law 5. Every point's (x, y, z, layer) triple is
computed from a fixed seed, so this script produces the same output shape on
every machine and every CI run.

Axes: x = anterior(+)/posterior(-), y = dorsal(+)/ventral(-), z = left/right.
Body length is normalised to ~1.0 along x. Only numpy is required - this
script runs with no connectome, no browser, and no network.
"""
import json
import random as _random
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent
OUT = ROOT / "web" / "data" / "bee.json"

SEED = 20260426          # frozen - changing this moves every point in bee.json
N_POINTS = 60000


class _RNG:
    """
    A numpy.random.Generator-shaped wrapper over the stdlib random module.

    Some machines running this script carry a local Application Control
    policy that blocks numpy.random's compiled extension (_bounded_integers
    / bit_generator) while leaving core numpy array math untouched. Rather
    than depend on numpy.random at all, this script draws every random
    number through Python's own random module - still seeded, still
    deterministic, still reproducible byte-for-byte given the same SEED -
    and only hands numpy an already-built array.
    """

    def __init__(self, seed):
        self._r = _random.Random(seed)

    def standard_normal(self, shape):
        if isinstance(shape, tuple):
            n = 1
            for s in shape:
                n *= s
            flat = [self._r.gauss(0.0, 1.0) for _ in range(n)]
            return np.array(flat, dtype=np.float64).reshape(shape)
        flat = [self._r.gauss(0.0, 1.0) for _ in range(shape)]
        return np.array(flat, dtype=np.float64)

    def random(self, k=None):
        if k is None:
            return self._r.random()
        return np.array([self._r.random() for _ in range(k)], dtype=np.float64)

LAYERS = ["head", "eye", "antenna", "thorax", "wing", "leg", "gaster", "sting"]
LAYER_ID = {name: i for i, name in enumerate(LAYERS)}
LAYER_COLOR = {
    "head": "#cfe7ff", "eye": "#7aa2c4", "antenna": "#cfe7ff",
    "thorax": "#cfe7ff", "wing": "#a8c7dd", "leg": "#cfe7ff",
    "gaster": "#cfe7ff", "sting": "#cfe7ff",
}
FRACTIONS = {
    "head": 0.09, "eye": 0.17, "antenna": 0.03, "thorax": 0.19,
    "wing": 0.14, "leg": 0.10, "gaster": 0.26, "sting": 0.02,
}


def _counts(n, fractions):
    names = list(fractions)
    raw = [fractions[k] * n for k in names]
    counts = [int(r) for r in raw]
    short = n - sum(counts)
    order = sorted(range(len(names)), key=lambda i: -raw[i])
    for i in range(short):
        counts[order[i % len(order)]] += 1
    return dict(zip(names, counts))


def shell_ellipsoid(rng, center, radii, k, shell=True):
    """k points gaussian-scattered inside (or on the shell of) an ellipsoid."""
    if k <= 0:
        return np.zeros((0, 3), dtype=np.float32)
    v = rng.standard_normal((k, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
    r = (0.86 + 0.14 * rng.random(k)) if shell else np.cbrt(rng.random(k))
    pts = v * r[:, None] * np.asarray(radii)
    return (pts + np.asarray(center)).astype(np.float32)


def banded_ellipsoid(rng, center, radii, k, bands=5, axis=0, contrast=0.72, shell=True):
    """
    Same as shell_ellipsoid, but density is modulated along one axis into
    stripes - a gaster's tergite bands rendered as point density, never as
    paint.
    """
    if k <= 0:
        return np.zeros((0, 3), dtype=np.float32)
    take, got = [], 0
    while got < k:
        batch = shell_ellipsoid(rng, (0, 0, 0), radii, max(k * 2, 256), shell=shell)
        t = (batch[:, axis] / radii[axis] + 1.0) / 2.0
        keep_p = (1 - contrast) + contrast * (0.5 + 0.5 * np.sin(t * bands * np.pi))
        keep = rng.random(len(batch)) < keep_p
        take.append(batch[keep])
        got += int(keep.sum())
    pts = np.concatenate(take, axis=0)[:k]
    return (pts + np.asarray(center)).astype(np.float32)


def bent_line(rng, p0, p1, p2, k, jitter=0.006, taper=None):
    """
    k points along a two-segment bent line p0->p1->p2 - an elbowed antenna
    or a jointed leg - gaussian-jittered off the line. `taper`, if given,
    scales jitter down toward the far end so a leg/antenna narrows.
    """
    if k <= 0:
        return np.zeros((0, 3), dtype=np.float32)
    p0, p1, p2 = (np.asarray(p, dtype=np.float64) for p in (p0, p1, p2))
    len1, len2 = np.linalg.norm(p1 - p0), np.linalg.norm(p2 - p1)
    frac1 = len1 / max(len1 + len2, 1e-9)
    n1 = int(round(k * frac1))
    n2 = k - n1
    t1, t2 = rng.random(n1), rng.random(n2)
    seg1 = p0[None, :] + (p1 - p0)[None, :] * t1[:, None]
    seg2 = p1[None, :] + (p2 - p1)[None, :] * t2[:, None]
    t_all = np.concatenate([t1, t2])
    pts = np.concatenate([seg1, seg2], axis=0)
    j = jitter * (1.0 - taper * t_all)[:, None] if taper else jitter
    pts = pts + rng.standard_normal(pts.shape) * j
    return pts.astype(np.float32)


def flat_sheet(rng, center, length, width, thickness, k):
    """A wing: a thin flat teardrop-ish point sheet, not a fly wing. `length`
    may be negative to point the sheet toward the tail (folded position)."""
    if k <= 0:
        return np.zeros((0, 3), dtype=np.float32)
    u = rng.random(k)                     # 0 root .. 1 tip
    taper = 1.0 - 0.55 * u                # narrower toward the tip
    v = (rng.random(k) * 2 - 1) * taper   # across the width
    x = u * length
    z = v * width * 0.5
    y = rng.standard_normal(k) * thickness
    pts = np.stack([x, y, z], axis=1)
    return (pts + np.asarray(center)).astype(np.float32)


def build(n=N_POINTS, seed=SEED):
    rng = _RNG(seed)
    counts = _counts(n, FRACTIONS)
    points, layer_ids = [], []

    def add(layer, arr):
        if len(arr) == 0:
            return
        points.append(arr)
        layer_ids.append(np.full(len(arr), LAYER_ID[layer], dtype=np.int8))

    # -- head + compound eyes -------------------------------------------
    add("head", shell_ellipsoid(rng, (0.38, 0.0, 0.0), (0.115, 0.115, 0.12),
                                 counts["head"]))
    n_eye = counts["eye"] // 2
    for side in (1, -1):
        add("eye", shell_ellipsoid(rng, (0.40, 0.015, side * 0.09),
                                    (0.065, 0.07, 0.05), n_eye))

    # -- elbowed antennae (scape forward, flagellum bends down-forward) --
    n_ant = counts["antenna"] // 2
    for side in (1, -1):
        base = (0.44, 0.03, side * 0.04)
        elbow = (0.58, 0.06, side * 0.08)
        tip = (0.68, -0.06, side * 0.14)
        add("antenna", bent_line(rng, base, elbow, tip, n_ant, jitter=0.006, taper=0.3))

    # -- mesosoma (thorax), tapering into the waist ----------------------
    add("thorax", shell_ellipsoid(rng, (0.13, 0.0, 0.0), (0.165, 0.135, 0.145),
                                   int(counts["thorax"] * 0.88)))
    add("thorax", shell_ellipsoid(rng, (-0.11, -0.01, 0.0), (0.045, 0.045, 0.045),
                                   counts["thorax"] - int(counts["thorax"] * 0.88),
                                   shell=False))

    # -- wings, folded flat along the dorsal midline ----------------------
    n_wing = counts["wing"] // 4
    for side in (1, -1):
        add("wing", flat_sheet(rng, (0.00, 0.11, side * 0.02), -0.46, 0.16, 0.006, n_wing))
        add("wing", flat_sheet(rng, (-0.06, 0.10, side * 0.02), -0.30, 0.10, 0.006, n_wing))

    # -- six legs; the mid pair carries the pollen-basket patch ------------
    leg_specs = [("fore", 0.20), ("mid", 0.06), ("hind", -0.10)]
    n_leg = counts["leg"] // 6
    for side in (1, -1):
        for name, x in leg_specs:
            hip = (x, -0.05, side * 0.13)
            knee = (x - 0.05, -0.22, side * 0.20)
            foot = (x - 0.02, -0.40, side * 0.18)
            k = int(n_leg * 0.8) if name == "mid" else n_leg
            add("leg", bent_line(rng, hip, knee, foot, k, jitter=0.008, taper=0.2))
            if name == "mid":
                basket_c = (x - 0.035, -0.30, side * 0.21)
                add("leg", shell_ellipsoid(rng, basket_c, (0.028, 0.05, 0.02),
                                            int(n_leg * 0.4), shell=False))

    # -- striped gaster + sting -------------------------------------------
    add("gaster", banded_ellipsoid(rng, (-0.32, -0.01, 0.0), (0.26, 0.155, 0.155),
                                    counts["gaster"], bands=5, axis=0, contrast=0.72))
    add("sting", bent_line(rng, (-0.56, -0.02, 0.0), (-0.62, -0.04, 0.0),
                            (-0.66, -0.05, 0.0), counts["sting"], jitter=0.004, taper=0.4))

    P = np.concatenate(points, axis=0)
    L = np.concatenate(layer_ids, axis=0)
    return P, L


def main():
    P, L = build()
    rows = [[round(float(x), 5), round(float(y), 5), round(float(z), 5), int(layer)]
            for (x, y, z), layer in zip(P, L)]
    layers_meta = {
        str(LAYER_ID[name]): {"name": name, "color": LAYER_COLOR[name],
                               "count": int((L == LAYER_ID[name]).sum())}
        for name in LAYERS
    }
    payload = {
        "points": rows,
        "layers": layers_meta,
        "meta": {
            "species": "Apis mellifera",
            "n_points": len(rows),
            "seed": SEED,
            "note": "Synthesized in code from body primitives. Not a scan, not a mesh, not a connectome.",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload), encoding="utf-8")
    print(f"wrote {OUT} - {len(rows):,} points")
    for name in LAYERS:
        print(f"  {name:8} {layers_meta[str(LAYER_ID[name])]['count']:>6,}")


if __name__ == "__main__":
    main()
