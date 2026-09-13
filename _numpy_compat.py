"""
Defensive numpy.random shim.

Some machines (this one included) run a local Application Control policy
that blocks numpy.random's compiled extensions (_bounded_integers,
bit_generator) while leaving the rest of numpy untouched. scipy.sparse
transitively imports numpy.random through its array-API compat layer, so
without this shim the mere act of `import scipy.sparse` - needed by
flysim.py, which this project keeps unmodified from fruitflydev/flycoinrh -
crashes on those machines before any of this project's own code runs.

On every other machine `import numpy.random` just succeeds and this file
installs nothing - it is a no-op there, and real numpy.random keeps doing
the real work. Where it does not succeed, a small stdlib-`random`-backed
stand-in is installed into sys.modules so the lazy `numpy.__getattr__
('random')` path resolves without ever touching the blocked DLLs. The
stand-in only implements the handful of Generator methods this project
actually calls (`random`, `standard_normal`, `choice`, `integers`) - it is
not a general numpy.random replacement - and it is still fully
deterministic per seed, so every "same seed -> same output" guarantee in
this codebase (dance.py's URL hash, antenna.py's odor hash, tests/) still
holds.

Import this before anything that imports scipy or calls np.random - it has
no effect if imported after the crash has already happened.
"""
import sys
import types


def ensure():
    if "numpy.random" in sys.modules:
        return
    try:
        import numpy.random  # noqa: F401
        return
    except Exception:
        pass

    import random as _random
    import numpy as _np

    class Generator:
        def __init__(self, seed=None):
            self._r = _random.Random(seed)

        def _n(self, size):
            n = 1
            for s in size:
                n *= s
            return n

        def random(self, size=None):
            if size is None:
                return self._r.random()
            if isinstance(size, tuple):
                flat = [self._r.random() for _ in range(self._n(size))]
                return _np.array(flat, dtype=_np.float64).reshape(size)
            return _np.array([self._r.random() for _ in range(size)], dtype=_np.float64)

        def standard_normal(self, size=None):
            if size is None:
                return self._r.gauss(0.0, 1.0)
            if isinstance(size, tuple):
                flat = [self._r.gauss(0.0, 1.0) for _ in range(self._n(size))]
                return _np.array(flat, dtype=_np.float64).reshape(size)
            return _np.array([self._r.gauss(0.0, 1.0) for _ in range(size)], dtype=_np.float64)

        def choice(self, a, size=None, replace=True):
            pool = list(range(a)) if isinstance(a, int) else list(a)
            if size is None:
                return self._r.choice(pool)
            out = [self._r.choice(pool) for _ in range(size)] if replace \
                else self._r.sample(pool, size)
            return _np.array(out)

        def integers(self, low, high=None, size=None):
            if high is None:
                low, high = 0, low
            if size is None:
                return self._r.randrange(low, high)
            return _np.array([self._r.randrange(low, high) for _ in range(size)])

    def default_rng(seed=None):
        return Generator(seed)

    mod = types.ModuleType("numpy.random")
    mod.default_rng = default_rng
    mod.Generator = Generator
    sys.modules["numpy.random"] = mod
    _np.random = mod   # so `np.random.default_rng` resolves without the lazy getattr
    print("numpy.random unavailable (Application Control policy) — "
          "using a deterministic stdlib-random stand-in.")


ensure()
