"""
ghost_sections/detectors.py -- pluggable ghost-tagging methods.

Each method takes the per-point feature table and returns a `GhostLabels`: a
per-point score in [0,1] plus a boolean decision. Methods are registered by
name, and the pipeline writes each method's output to Plots/<method_name>/, so
adding a method is one decorated function and nothing else.

    @register("method5_myidea", "one-line description")
    def method5_myidea(pf, geom, **kw) -> GhostLabels:
        ...

Design rule: a method may NEVER mutate the features it is given. Comparisons
across methods are only meaningful if they all see identical input.

None of these are validated against labelled ground truth -- there is none yet.
They are hypotheses with a physical rationale, and `agreement_matrix()` exists
so you can at least see where they disagree, which is where the interesting
cases live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from .features import PointFeatures

_REGISTRY: Dict[str, "GhostMethod"] = {}


@dataclass
class GhostLabels:
    name: str
    score: np.ndarray
    """Per-point ghost likelihood in [0,1]. Not calibrated -- ordering only."""
    is_ghost: np.ndarray
    description: str = ""
    params: Dict = field(default_factory=dict)
    rationale: str = ""

    @property
    def n_ghosts(self) -> int:
        return int(self.is_ghost.sum())

    @property
    def fraction(self) -> float:
        return float(self.is_ghost.mean()) if self.is_ghost.size else 0.0

    def report(self) -> str:
        return ("%-22s %5d / %5d points tagged (%.1f%%)  -- %s"
                % (self.name, self.n_ghosts, self.is_ghost.size,
                   100 * self.fraction, self.description))


@dataclass
class GhostMethod:
    name: str
    description: str
    fn: Callable

    def __call__(self, pf: PointFeatures, geom, **kw) -> GhostLabels:
        return self.fn(pf, geom, **kw)


def register(name: str, description: str):
    def deco(fn):
        _REGISTRY[name] = GhostMethod(name, description, fn)
        return fn
    return deco


def available() -> List[str]:
    return sorted(_REGISTRY)


def get(name: str) -> GhostMethod:
    if name not in _REGISTRY:
        raise KeyError("unknown ghost method %r. Available: %s"
                       % (name, ", ".join(available())))
    return _REGISTRY[name]


def _empty(name, desc, n):
    return GhostLabels(name, np.zeros(n), np.zeros(n, bool), desc)


# ---------------------------------------------------------------------------
# Methods
# ---------------------------------------------------------------------------


@register("method0_none",
          "no filtering -- every point kept, for the raw baseline")
def method0_none(pf: PointFeatures, geom, **kw) -> GhostLabels:
    n = len(pf)
    return GhostLabels(
        "method0_none", np.zeros(n), np.zeros(n, bool),
        "no filtering (baseline)",
        rationale="Establishes what the device actually reported, before any "
                  "host-side judgement. Every other method is measured against "
                  "this.")


@register("method1_mti_filter",
          "moving-target indication: drop near-zero-Doppler (static) returns")
def method1_mti_filter(pf: PointFeatures, geom,
                       zero_bins: int = 1, **kw) -> GhostLabels:
    """Not a ghost detector -- a clutter remover, included as the control.

    It removes walls and furniture, which is why it looks impressive. It cannot
    remove a ghost of a MOVING person, because that ghost is moving too. If a
    method appears to beat MTI, check it is not just removing more static.
    """
    n = len(pf)
    if n == 0:
        return _empty("method1_mti_filter", "MTI", 0)
    static = np.abs(pf.doppler_bin) <= zero_bins
    score = static.astype(float)
    return GhostLabels(
        "method1_mti_filter", score, static,
        "static (|doppler bin| <= %d) tagged" % zero_bins,
        params=dict(zero_bins=zero_bins),
        rationale="Clutter control, NOT a ghost test: a specular ghost of a "
                  "moving target is itself moving and survives this untouched.")


@register("method2_cocell_secondary",
          "device-declared secondary angular peaks (multiObjBeamForming)")
def method2_cocell_secondary(pf: PointFeatures, geom, **kw) -> GhostLabels:
    """The strongest prior available, and it costs nothing to compute.

    Two points in one range-Doppler cell means the DSP found a second angular
    peak there. Physically that is one scatterer plus either a genuine second
    scatterer at the same range and speed, or a specular image of the first.
    """
    n = len(pf)
    if n == 0:
        return _empty("method2_cocell_secondary", "co-cell", 0)
    is_g = pf.is_cocell_secondary.copy()
    score = np.where(is_g, np.clip(pf.cocell_count / 3.0, 0, 1), 0.0)
    return GhostLabels(
        "method2_cocell_secondary", score, is_g,
        "secondary peaks sharing a range-Doppler cell",
        rationale="multiObjBeamForming (threshold 0.5) explicitly emits "
                  "secondary angular peaks per range-Doppler cell. Turning it "
                  "off in the .cfg and re-capturing is the controlled test.")


@register("method3_snr_range_anomaly",
          "returns that are weak for their range after removing the 1/R^4 trend")
def method3_snr_range_anomaly(pf: PointFeatures, geom,
                              snr_excess_db: float = -3.0,
                              min_range_gain_m: float = 0.5, **kw
                              ) -> GhostLabels:
    """A ghost pays an extra reflection loss on top of ordinary range loss.

    Requires BOTH an SNR deficit relative to the range trend AND a position
    behind the frame's primary target, because a weak return that is nearer than
    the target cannot be that target's ghost.
    """
    n = len(pf)
    if n == 0:
        return _empty("method3_snr_range_anomaly", "snr anomaly", 0)
    weak = np.isfinite(pf.snr_excess_db) & (pf.snr_excess_db <= snr_excess_db)
    behind = np.isfinite(pf.d_range_to_primary) & \
        (pf.d_range_to_primary >= min_range_gain_m)
    is_g = weak & behind
    deficit = np.clip(-np.nan_to_num(pf.snr_excess_db) / 12.0, 0, 1)
    return GhostLabels(
        "method3_snr_range_anomaly", np.where(is_g, deficit, 0.0), is_g,
        "SNR excess <= %.1f dB and >= %.2f m behind the primary"
        % (snr_excess_db, min_range_gain_m),
        params=dict(snr_excess_db=snr_excess_db,
                    min_range_gain_m=min_range_gain_m),
        rationale="Each specular bounce costs energy. The range-trend removal "
                  "is what stops this degenerating into a range gate.")


@register("method4_mirror_geometry",
          "points behind the primary whose Doppler tracks it -- specular images")
def method4_mirror_geometry(pf: PointFeatures, geom,
                            min_range_gain_m: float = 0.5,
                            max_range_gain_m: float = 6.0,
                            v_tol_mps: Optional[float] = None,
                            min_abs_v: float = 0.15,
                            min_azimuth_sep_deg: float = 8.0, **kw
                            ) -> GhostLabels:
    """The most specific of the geometric tests.

    A specular image of a moving target: (a) sits farther than the target,
    always; (b) carries a Doppler that is a fixed geometric multiple of the
    target's, so for small angles it tracks it closely; (c) arrives from a
    different direction -- the reflection point, not the target.

    Requiring an angular separation is what stops this tagging the target's own
    limbs, which share the range and the Doppler but not the bearing.
    """
    n = len(pf)
    if n == 0:
        return _empty("method4_mirror_geometry", "mirror", 0)
    if v_tol_mps is None:
        v_tol_mps = max(3.0 * geom.doppler_resolution_mps, 0.1)

    dr = pf.d_range_to_primary
    dv = pf.d_v_to_primary
    da = np.abs(pf.d_azimuth_to_primary)

    behind = np.isfinite(dr) & (dr >= min_range_gain_m) & (dr <= max_range_gain_m)
    tracks = np.isfinite(dv) & (np.abs(dv) <= v_tol_mps)
    moving = np.abs(pf.v) >= min_abs_v
    elsewhere = np.isfinite(da) & (da >= min_azimuth_sep_deg)

    is_g = behind & tracks & moving & elsewhere
    prox = 1.0 - np.clip(np.abs(np.nan_to_num(dv)) / max(v_tol_mps, 1e-9), 0, 1)
    return GhostLabels(
        "method4_mirror_geometry", np.where(is_g, prox, 0.0), is_g,
        "%.2f-%.2f m behind primary, |dv|<=%.3f m/s, |daz|>=%.0f deg, moving"
        % (min_range_gain_m, max_range_gain_m, v_tol_mps, min_azimuth_sep_deg),
        params=dict(min_range_gain_m=min_range_gain_m,
                    max_range_gain_m=max_range_gain_m,
                    v_tol_mps=v_tol_mps, min_abs_v=min_abs_v,
                    min_azimuth_sep_deg=min_azimuth_sep_deg),
        rationale="Specular images are farther, Doppler-correlated, and "
                  "angularly displaced. The angular test separates a ghost "
                  "from the target's own limbs.")


@register("method5_consensus",
          "union of the geometric and amplitude tests, excluding pure MTI")
def method5_consensus(pf: PointFeatures, geom, min_votes: int = 2, **kw
                      ) -> GhostLabels:
    """Combine the independent evidence.

    MTI is deliberately excluded from the vote: it tags static clutter, which
    would swamp the count and conflate 'not a person' with 'not real'.
    """
    n = len(pf)
    if n == 0:
        return _empty("method5_consensus", "consensus", 0)
    voters = ["method2_cocell_secondary", "method3_snr_range_anomaly",
              "method4_mirror_geometry"]
    votes = np.zeros(n, int)
    acc = np.zeros(n)
    for v in voters:
        lab = get(v)(pf, geom)
        votes += lab.is_ghost.astype(int)
        acc += lab.score
    is_g = votes >= min_votes
    return GhostLabels(
        "method5_consensus", np.clip(acc / len(voters), 0, 1), is_g,
        ">= %d of %d independent tests agree" % (min_votes, len(voters)),
        params=dict(min_votes=min_votes, voters=voters),
        rationale="The three voters use different physics (device secondary "
                  "peaks, amplitude, geometry), so agreement is meaningful "
                  "in a way that repeating one test would not be.")


# ---------------------------------------------------------------------------


def agreement_matrix(pf: PointFeatures, geom,
                     methods: Optional[List[str]] = None) -> str:
    """Pairwise Jaccard overlap between methods.

    With no ground truth, disagreement is the most informative thing available:
    points that one method tags and another does not are where to look first.
    """
    methods = methods or [m for m in available() if m != "method0_none"]
    labs = {m: get(m)(pf, geom) for m in methods}
    w = max(len(m) for m in methods) + 2
    lines = ["Pairwise agreement (Jaccard, tagged sets):",
             " " * w + "".join("%10s" % m.split("_")[0] for m in methods)]
    for a in methods:
        row = ["%-*s" % (w, a)]
        for b in methods:
            A, B = labs[a].is_ghost, labs[b].is_ghost
            u = (A | B).sum()
            row.append("%10s" % ("%.2f" % ((A & B).sum() / u) if u else "-"))
        lines.append("".join(row))
    lines.append("")
    for m in methods:
        lines.append("  " + labs[m].report())
    return "\n".join(lines)
