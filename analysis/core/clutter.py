"""
core/clutter.py -- background / clutter suppression.

The reference paper (§2.3) offers two options:

    "Static clutter is suppressed either by subtraction of an empty-scene
     estimate RD0(r,v) or by removing bins near zero radial velocity (v ~ 0),
     producing the clutter-suppressed map RD'(r,v,t)."

Both are implemented. Empty-scene subtraction is the stronger of the two and is
what Yogi's MATLAB uses (`bg_mean = mean(bg_adc,1); wk_bs = wk_adc - bg_mean`),
but it requires a matched empty recording captured with the SAME config in the
SAME room -- otherwise it injects structure rather than removing it.

For through-wall work the zero-Doppler notch is a blunt instrument: a stationary
person still breathes, and a specular ghost of a moving person is itself moving,
so the notch removes wall returns but not ghosts. It is a clutter tool, not a
ghost tool. Ghost handling lives in ghost_sections/.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from .maps import TimeMap
from .recording import Recording


def notch_zero_doppler(rec: Recording, half_width_bins: int = 1
                       ) -> np.ndarray:
    """Boolean mask over the point cloud: True = keep (moving).

    Removes points whose Doppler bin is within `half_width_bins` of zero.
    With `clutterRemoval` disabled on-chip -- the usual case -- static returns
    dominate the point cloud, so this is the cheapest large win.
    """
    g = rec.geom
    out = []
    for p in rec.points:
        if p is None or p.size == 0:
            out.append(np.zeros(0, bool))
            continue
        b = np.round(p["doppler"].astype(float)
                     / max(g.doppler_resolution_mps, 1e-12))
        out.append(np.abs(b) > half_width_bins)
    return np.concatenate(out) if out else np.zeros(0, bool)


def notch_rd_map(rd: np.ndarray, num_doppler_bins: int,
                 half_width_bins: int = 1) -> np.ndarray:
    """Zero the near-zero-Doppler rows of an RD cube (frames, dop, range)."""
    out = rd.copy()
    c = num_doppler_bins // 2
    lo = max(0, c - half_width_bins)
    hi = min(num_doppler_bins, c + half_width_bins + 1)
    out[:, lo:hi, :] = 0.0
    return out


def subtract_empty_scene(rec: Recording, background: Recording,
                         strict: bool = True) -> Recording:
    """Subtract an empty-scene estimate from the RD map (paper §2.3).

    Only meaningful at TIER B. A point cloud cannot be background-subtracted --
    the subtraction has to happen before detection, and detection already
    happened on the chip.
    """
    if rec.rd_map is None or background.rd_map is None:
        raise ValueError(
            "empty-scene subtraction needs a range-Doppler map in BOTH "
            "recordings (TLV 5 / rangeDopplerHeatMap 1). This capture is "
            "tier %r. Use notch_zero_doppler() instead, or re-capture with "
            "the heat map enabled." % rec.tier)

    if strict:
        a, b = rec.geom, background.geom
        for attr in ("num_range_bins", "num_doppler_bins"):
            if getattr(a, attr) != getattr(b, attr):
                raise ValueError(
                    "geometry mismatch on %s (%s vs %s): the background must be "
                    "captured with the same .cfg, or subtraction injects "
                    "structure instead of removing it."
                    % (attr, getattr(a, attr), getattr(b, attr)))

    rd0 = np.nanmean(background.rd_map, axis=0)      # (dop, range)
    out = np.copy(rec.rd_map)
    out -= rd0[None, :, :]
    np.clip(out, 0.0, None, out=out)

    import copy
    new = copy.copy(rec)
    new.rd_map = out
    new.name = rec.name + "+bgsub"
    return new


def subtract_map_baseline(m: TimeMap, method: str = "median",
                          percentile: float = 20.0) -> TimeMap:
    """Remove the time-invariant component of an RT/VT map.

    A pragmatic stand-in for empty-scene subtraction when no empty recording
    exists: estimate each row's static level across time and remove it. Walls
    and furniture are constant in range, so this suppresses them; a walking
    person is not, so it survives.

    method: 'median' (robust) | 'mean' | 'percentile'
    """
    d = m.data
    if method == "median":
        base = np.nanmedian(d, axis=1, keepdims=True)
    elif method == "mean":
        base = np.nanmean(d, axis=1, keepdims=True)
    elif method == "percentile":
        base = np.nanpercentile(d, percentile, axis=1, keepdims=True)
    else:
        raise ValueError("unknown method %r" % method)

    out = d - base
    if not m.value_label.endswith("dB"):
        out = np.clip(out, 0.0, None)
    return TimeMap(out, m.axis, m.t, m.axis_label, m.value_label, m.tier,
                   m.provenance + " | baseline removed per row (%s)" % method)


def find_background_recording(rec_path: str,
                              search_root: Optional[str] = None
                              ) -> Optional[Tuple[str, str]]:
    """Look for an empty-scene capture beside this one.

    Convention: a sibling run directory whose name contains 'empty'. Mirrors
    the reference dataset layout (`two_people_chris_empty...`).
    """
    import os
    root = search_root or os.path.dirname(os.path.dirname(
        os.path.abspath(rec_path)))
    if not os.path.isdir(root):
        return None
    for d in sorted(os.listdir(root)):
        if "empty" not in d.lower():
            continue
        full = os.path.join(root, d)
        if not os.path.isdir(full):
            continue
        dats = [f for f in os.listdir(full) if f.lower().endswith(".dat")]
        cfgs = [f for f in os.listdir(full) if f.lower().endswith(".cfg")]
        if len(dats) == 1 and len(cfgs) == 1:
            return (os.path.join(full, dats[0]), os.path.join(full, cfgs[0]))
    return None
