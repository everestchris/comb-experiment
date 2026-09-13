"""
A dance-first front end for the shared LIF body.

Apis mellifera's antennal lobe carries on the order of 160-166 identified
glomeruli (Galizia lab honeybee AL atlas work). 170 is used here as a round
modelling scaffold - the same kind of hashed-switchboard stand-in count
antenna.py used in the earlier ant fork of this project - not a claim that
the loaded connectome carries 170 real Apis glomeruli. No public, complete
Apis EM graph exists at all (see README, line 1).

Two senses live here, both wired onto the shared LIF body:

* Odor - a handful of named scalars about the current page (is this domain
  new, did the last hop come back 200, how novel is the path) hashed onto a
  fixed 170-channel space.
* Polarization - a single heading/hour-angle scalar, standing in for the
  dorsal-rim-area polarization compass real bees use to read the sky's
  polarized-light pattern and fold it into the waggle angle (von Frisch;
  Rossel & Wehner 1984). If the loaded graph carries annotated dorsal-rim /
  polarization-sensitive photoreceptor types, this drives those neurons -
  checked directly against whatever graph is actually loaded, not assumed.
  Measured against the full FlyEM male CNS v1.0 release: it does carry both
  ORN/PN-annotated olfactory neurons (2,641 of them) and R7d/R8d dorsal-rim
  photoreceptors (248), so both channels here drive real fly chemosensory
  and polarization-compass cells - real fly anatomy, never bee anatomy, and
  the two are not the same claim. Only a graph with neither would fall back
  to the reserved pool below, printing a one-line disclaimer the first time
  that happens.

Both hashes are salted (comb.toml: [antenna].salt) and never learned - the
only weights this project ever moves live in mushroom.py. There is no
screenshot retina driving anything here; a low-res luminance sample could be
added later as a secondary drive (a bee does have eyes), but the dance itself
must never be steered by FlyEye-style hex-column vision - see README.
"""
import hashlib
import re

import numpy as np

GLOMERULI = 170             # Apis mellifera AL scale, rounded (see module docstring)
DEFAULT_ODORS = ("domain", "path_tokens", "novelty", "status_ok")
NO_ANTENNA_MSG = "NO ANTENNA CELLS IN THIS GRAPH — driving reserved channels, not anatomy."
NO_POL_MSG = "NO DORSAL-RIM CELLS IN THIS GRAPH — driving reserved channels, not anatomy."

# Cell-type / superclass regexes that would indicate a real chemosensory or
# polarization-compass pathway is present in the loaded graph. Kept narrow on
# purpose: a false positive here would mean claiming anatomy that is not
# there. Checked directly at runtime, never assumed - the full FlyEM male
# CNS v1.0 release does match both (2,641 ORN/PN cells, 248 dorsal-rim
# photoreceptors), so on that graph neither falls back to the reserved pool.
_OLFACTORY_TYPE_RE = r"^(ORN|PN)\d|^ORN_|^PN_|^adPN|^lPN|^vPN|_ORN$|_PN$"
_OLFACTORY_SUPERCLASS_RE = r"olfactory|antennal"

_POL_TYPE_RE = r"^R[78]d|DRA|_DRA$|^POL"
_POL_SUPERCLASS_RE = r"polarization|dorsal.?rim"

# Cell types this file must never quietly annex, even as a reserved pool,
# because other modules read them out for dance/recruit decoding (hive.py)
# or own them outright (mushroom.py's own circuit).
_RESERVED_EXCLUDE_RE = r"^(KC|MBON|PAM|PPL1)|^DNa0[12]$|^MDN$|^DNp09$|^MN9$"


def glomeruli_for_odor(name, salt, glomeruli=GLOMERULI, per_odor=8):
    """
    Deterministic odor -> glomerulus-index hash.

    Pure function of (name, salt) - no randomness beyond what the hash
    fixes, no state, nothing to train. The same odor name always resolves to
    the same glomeruli, in this process and in the next one, as long as the
    salt in comb.toml does not change.
    """
    per_odor = max(1, min(per_odor, glomeruli))
    digest = hashlib.sha256(f"{salt}:{name}".encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big")
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(glomeruli, size=per_odor, replace=False))


def _match_pool(fb, type_re, superclass_re):
    idx = fb.where(type_re=type_re)
    if len(idx) == 0:
        try:
            sc = np.asarray(fb.superclass).astype(str)
            rx = re.compile(superclass_re, re.I)
            mask = np.array([bool(rx.search(s)) for s in sc])
            idx = np.flatnonzero(mask)
        except Exception:
            idx = np.array([], dtype=np.int64)
    return np.sort(np.unique(idx))


def _reserved_pool(fb, taken=None):
    """Untyped, otherwise-unclaimed bodies - honest filler, not anatomy."""
    types = np.asarray(fb.types).astype(str)
    untyped = types == ""
    excluded = fb.where(type_re=_RESERVED_EXCLUDE_RE)
    excl_mask = np.zeros(fb.n, dtype=bool)
    excl_mask[excluded] = True
    if taken is not None and len(taken):
        excl_mask[taken] = True
    return np.flatnonzero(untyped & ~excl_mask)


class Antenna:
    """170 glomerular odor channels, plus one polarization-compass channel."""

    def __init__(self, fb, salt, glomeruli=GLOMERULI, per_odor=8, max_hz=180.0):
        self.fb = fb
        self.salt = salt
        self.glomeruli = glomeruli
        self.per_odor = per_odor
        self.max_hz = max_hz

        pool = _match_pool(fb, _OLFACTORY_TYPE_RE, _OLFACTORY_SUPERCLASS_RE)
        self.anatomical = len(pool) > 0
        if not self.anatomical:
            print(NO_ANTENNA_MSG)
            pool = _reserved_pool(fb)
        self.pool = pool
        # Partition the pool into `glomeruli` buckets. If the pool is smaller
        # than 170, some channels get an empty bucket and simply drive
        # nothing when hit - that is honest under-resolution, not an error.
        self._buckets = (
            [b for b in np.array_split(pool, glomeruli)]
            if len(pool) else [np.array([], dtype=np.int64)] * glomeruli
        )

        pol_pool = _match_pool(fb, _POL_TYPE_RE, _POL_SUPERCLASS_RE)
        self.pol_anatomical = len(pol_pool) > 0
        if not self.pol_anatomical:
            print(NO_POL_MSG)
            pol_pool = _reserved_pool(fb, taken=pool)
        self.pol_pool = pol_pool

        self._glom_cache = {}
        self.last_al = np.zeros(glomeruli, dtype=np.float32)
        self.last_pol = 0.0

    def _glomeruli_for(self, name):
        g = self._glom_cache.get(name)
        if g is None:
            g = glomeruli_for_odor(name, self.salt, self.glomeruli, self.per_odor)
            self._glom_cache[name] = g
        return g

    def smell(self, odors):
        """
        odors: dict[str, float], each value in 0..1.

        Returns a drive dict compatible with FlyBrain.run(drive=...): keys
        are tuples of neuron indices, values are a scalar rate in Hz.
        """
        per_glom = np.zeros(self.glomeruli, dtype=np.float32)
        for name, value in odors.items():
            v = float(np.clip(value, 0.0, 1.0))
            if v <= 0.0:
                continue
            gloms = self._glomeruli_for(name)
            per_glom[gloms] = np.maximum(per_glom[gloms], v)

        self.last_al = per_glom
        hz = per_glom * self.max_hz
        drive = {}
        for g in np.flatnonzero(hz > 0.0):
            idx = self._buckets[g]
            if len(idx):
                drive[tuple(int(i) for i in idx)] = float(hz[g])
        return drive

    def polarize(self, heading_rad, hour_angle_rad):
        """
        heading_rad: this bee's current waggle-plane heading.
        hour_angle_rad: the sun's stand-in hour angle right now (dance.py).

        A polarization-compass reading is honestly a match between the two,
        not either scalar alone - real dorsal-rim ommatidia fire hardest
        when the overhead polarization pattern lines up with the body's own
        axis (Rossel & Wehner 1984). cos() of the difference is the simplest
        function with that shape; it is a modelling choice, not a
        measurement, and it is never learned.
        """
        v = float(np.clip((np.cos(heading_rad - hour_angle_rad) + 1.0) / 2.0, 0.0, 1.0))
        self.last_pol = v
        if not len(self.pol_pool):
            return {}
        return {tuple(int(i) for i in self.pol_pool): float(v * self.max_hz)}

    def al_bar(self):
        """Last computed per-glomerulus activation, 0..1, for the 170-bar UI."""
        return [round(float(x), 4) for x in self.last_al]

    def pol_reading(self):
        return round(float(self.last_pol), 4)
