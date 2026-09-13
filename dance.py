"""
Waggle kinematics.

A waggle bout has three numbers: which way (angle_rad), how long
(duration_s) and how hard (vigor). None of the three is read off a chain or
a wallet - COMB_LIVE does not exist anywhere in this process (see README,
hard law 3) - they are read off which page the dancer is telling the hive
about.

The mapping from "a URL on the web" to "a heading on the comb" is a
MODELLING CHOICE, spelled out here and in README, not a measurement of
anything a real bee does:

    angle_rad  = frozen_hash(url) % 360, in degrees, then converted to
                 radians. In the field a forager encodes the solar bearing
                 to a flower patch onto the vertical comb face; here the
                 "bearing" is a fixed function of the URL alone, so the
                 same page always dances at the same angle in every run, on
                 every machine. hive.py hashes the NEXT page a worker is
                 about to visit, not the one it is currently reporting from
                 - the dance points at where it is headed.
    duration_s = clip(0.4 + 0.35 * hop_distance, 0.4, 4.0) - real waggle
                 runs lengthen with true foraging distance (von Frisch's
                 distance code, roughly linear once calibrated for a race of
                 bees); hop_distance (links away from the nest seed) stands
                 in for distance here.
    vigor      = clip(0.3 + 0.5 * novelty, 0, 1) - real bees dance more
                 vigorously, with shorter return phases, for a richer source
                 (Seeley 1995); novelty here is how unfamiliar the target
                 page is to this worker (hive.py), never a price and never
                 a trade.

waggle_ms (duration_s in milliseconds) drives how long web/js/waggle.js
oscillates the specimen's abdomen; angle_rad rotates its long axis. Neither
number ever reaches back into the sim - this module only produces bouts, it
never consumes one.
"""
import hashlib
import math
import time
from dataclasses import dataclass
from urllib.parse import urlparse

DURATION_MIN = 0.4
DURATION_MAX = 4.0
DANCE_SALT = "comb-beta.0-dance-salt-v1"


@dataclass
class DanceBout:
    url: str
    angle_rad: float
    duration_s: float
    vigor: float
    waggle_ms: int


def angle_deg_for_url(url, salt=DANCE_SALT):
    """Frozen host+path -> hash -> degrees, 0..359. The literal formula."""
    host = (urlparse(url).hostname or "").lower()
    path = urlparse(url).path or "/"
    key = f"{host}{path}"
    d = hashlib.sha256(f"{salt}:{key}".encode("utf-8")).digest()
    return int.from_bytes(d[:8], "big") % 360


def angle_for_url(url, salt=DANCE_SALT):
    """
    Frozen host+path -> heading in radians, via angle_deg_for_url().

    Pure function of the URL and the salt - the same page dances at the
    same angle in this process and in the next one, as long as the salt
    does not change.
    """
    return math.radians(angle_deg_for_url(url, salt=salt))


def duration_for_hops(hop_distance):
    return float(min(DURATION_MAX, max(DURATION_MIN, 0.4 + 0.35 * hop_distance)))


def vigor_for_signal(signal):
    return float(min(1.0, max(0.0, signal)))


def build_bout(url, hop_distance, signal, salt=DANCE_SALT):
    """One waggle bout for the dancer's current page."""
    duration_s = duration_for_hops(hop_distance)
    vigor = vigor_for_signal(signal)
    angle_rad = angle_for_url(url, salt=salt)
    waggle_ms = int(round(duration_s * 1000))
    return DanceBout(url=url, angle_rad=angle_rad, duration_s=duration_s,
                      vigor=vigor, waggle_ms=waggle_ms)


def hour_angle_rad(ts=None):
    """
    A stand-in solar hour angle - this process has no geolocation.

    Real hour angle is (local solar time - 12h) * 15 deg/h. Lacking a real
    longitude, wall-clock UTC seconds-of-day is used instead - a modelling
    choice, not an ephemeris. Kept as a function of time only so
    Antenna.polarize()'s reading actually drifts over the course of a run,
    the way a real polarization pattern drifts with the sun.
    """
    t = ts if ts is not None else time.time()
    frac = (t % 86400.0) / 86400.0
    return (frac - 0.5) * 2 * math.pi
