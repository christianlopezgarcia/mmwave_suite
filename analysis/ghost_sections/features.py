"""
ghost_sections/features.py -- per-point features for multipath discrimination.

Physical basis
--------------
A specular multipath return travels radar -> (wall) -> target -> radar rather
than radar -> target -> radar. Three consequences are exploitable, and all three
survive in the TI point cloud:

1. RANGE.    The measured range is half the total path length, so a ghost always
             appears FARTHER than its parent. Never nearer. This is the single
             most reliable discriminator and it is geometry, not statistics.

2. ANGLE.    The ghost arrives from the direction of the reflection point on the
             wall, not from the target. Against a flat wall it is the mirror
             image of the target about the wall plane.

3. AMPLITUDE. Each specular bounce costs energy, so a ghost's SNR is below its
             parent's -- on top of the ordinary 1/R^4 range loss. Comparing SNR
             *after* removing the range trend is what makes this usable.

A fourth signature is specific to this sensor rather than to physics:
`multiObjBeamForming` deliberately reports secondary angular peaks within the
same range-Doppler cell (mmWave.js / TI mmw demo, threshold 0.5 by default).
Any two points sharing a range-Doppler cell are therefore one dominant return
plus a device-declared secondary -- an extremely strong ghost prior, and free.

Nothing here decides anything. These are features; the deciding lives in
detectors.py so that methods can be swapped and compared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..core.recording import Recording


@dataclass
class PointFeatures:
    """One row per detected point, across the whole recording."""

    t: np.ndarray
    frame_idx: np.ndarray
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    v: np.ndarray
    range: np.ndarray
    azimuth_deg: np.ndarray
    elevation_deg: np.ndarray
    snr_db: np.ndarray
    noise_db: np.ndarray

    # --- derived -------------------------------------------------------
    range_bin: np.ndarray = None
    doppler_bin: np.ndarray = None
    cocell_count: np.ndarray = None
    """How many points share this point's (range bin, Doppler bin) cell."""
    is_cocell_secondary: np.ndarray = None
    """True if this point is NOT the strongest in its shared cell."""
    snr_excess_db: np.ndarray = None
    """SNR minus the recording's median SNR at that range -- removes the 1/R^4
    trend so a genuinely weak return is distinguishable from a merely distant."""
    d_range_to_primary: np.ndarray = None
    d_azimuth_to_primary: np.ndarray = None
    d_v_to_primary: np.ndarray = None
    primary_range: np.ndarray = None
    """Per-frame dominant-target range, broadcast to every point in the frame."""

    def __len__(self) -> int:
        return int(self.t.size)

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if v is not None}


def compute_features(rec: Recording,
                     primary_snr_percentile: float = 75.0,
                     range_trend_bins: int = 16) -> PointFeatures:
    """Build the full per-point feature table for a recording."""
    f = rec.flat()
    g = rec.geom
    pf = PointFeatures(**{k: f[k] for k in
                          ("t", "frame_idx", "x", "y", "z", "v", "range",
                           "azimuth_deg", "elevation_deg", "snr_db",
                           "noise_db")})
    n = len(pf)
    if n == 0:
        for a in ("range_bin", "doppler_bin", "cocell_count",
                  "is_cocell_secondary", "snr_excess_db", "d_range_to_primary",
                  "d_azimuth_to_primary", "d_v_to_primary", "primary_range"):
            setattr(pf, a, np.zeros(0))
        return pf

    pf.range_bin = np.round(pf.range / max(g.range_idx_to_meters, 1e-12)).astype(int)
    pf.doppler_bin = np.round(pf.v / max(g.doppler_resolution_mps, 1e-12)).astype(int)

    _cocell(pf)
    _snr_excess(pf, g, range_trend_bins)
    _primary_relative(pf, rec, primary_snr_percentile)
    return pf


def _cocell(pf: PointFeatures) -> None:
    """Flag device-declared secondary angular peaks.

    Grouping is per FRAME -- two points in the same cell in different frames are
    unrelated. The strongest point in a shared cell is treated as the primary
    and the rest as secondaries; where SNR is unavailable the first is kept.
    """
    n = len(pf)
    count = np.ones(n, dtype=int)
    secondary = np.zeros(n, dtype=bool)

    order = np.lexsort((pf.doppler_bin, pf.range_bin, pf.frame_idx))
    key = np.stack([pf.frame_idx[order], pf.range_bin[order],
                    pf.doppler_bin[order]], axis=1)
    # boundaries of runs of identical (frame, range bin, doppler bin)
    new = np.ones(n, bool)
    if n > 1:
        new[1:] = np.any(key[1:] != key[:-1], axis=1)
    grp = np.cumsum(new) - 1
    sizes = np.bincount(grp)
    count[order] = sizes[grp]

    for gi in np.flatnonzero(sizes > 1):
        members = order[grp == gi]
        s = pf.snr_db[members]
        best = members[int(np.nanargmax(s))] if np.isfinite(s).any() else members[0]
        for m in members:
            if m != best:
                secondary[m] = True

    pf.cocell_count = count
    pf.is_cocell_secondary = secondary


def _snr_excess(pf: PointFeatures, geom, n_bins: int) -> None:
    """SNR relative to the typical SNR at the same range.

    Without this, every distant point looks weak and every near point looks
    strong, and an SNR threshold just re-implements a range gate.
    """
    r = pf.range
    if not np.isfinite(pf.snr_db).any():
        pf.snr_excess_db = np.zeros(len(pf))
        return
    lo, hi = float(np.nanmin(r)), float(np.nanmax(r))
    if hi <= lo:
        pf.snr_excess_db = pf.snr_db - np.nanmedian(pf.snr_db)
        return
    edges = np.linspace(lo, hi + 1e-9, n_bins + 1)
    idx = np.clip(np.digitize(r, edges) - 1, 0, n_bins - 1)
    trend = np.full(n_bins, np.nan)
    for b in range(n_bins):
        m = idx == b
        if m.sum() >= 3:
            trend[b] = np.nanmedian(pf.snr_db[m])
    ok = np.isfinite(trend)
    if ok.sum() >= 2:
        centres = 0.5 * (edges[:-1] + edges[1:])
        trend = np.interp(centres, centres[ok], trend[ok])
    else:
        trend = np.full(n_bins, np.nanmedian(pf.snr_db))
    pf.snr_excess_db = pf.snr_db - trend[idx]


def _primary_relative(pf: PointFeatures, rec: Recording,
                      snr_percentile: float) -> None:
    """Per-frame dominant target, and each point's offset from it.

    The primary is the NEAREST point among the frame's strong returns (SNR above
    the given percentile). Nearest rather than strongest, because a specular
    ghost can momentarily out-shine its parent, but by geometry it can never be
    closer than it.
    """
    n = len(pf)
    d_r = np.full(n, np.nan)
    d_a = np.full(n, np.nan)
    d_v = np.full(n, np.nan)
    p_r = np.full(n, np.nan)

    order = np.argsort(pf.frame_idx, kind="stable")
    fi = pf.frame_idx[order]
    bounds = np.searchsorted(fi, np.arange(rec.num_frames + 1))

    for i in range(rec.num_frames):
        sl = order[bounds[i]:bounds[i + 1]]
        if sl.size == 0:
            continue
        r = pf.range[sl]
        s = pf.snr_db[sl]
        if np.isfinite(s).any() and sl.size > 2:
            thr = np.nanpercentile(s, snr_percentile)
            strong = sl[np.isfinite(s) & (s >= thr)]
            if strong.size == 0:
                strong = sl
        else:
            strong = sl
        prim = strong[int(np.argmin(pf.range[strong]))]
        p_r[sl] = pf.range[prim]
        d_r[sl] = pf.range[sl] - pf.range[prim]
        d_a[sl] = pf.azimuth_deg[sl] - pf.azimuth_deg[prim]
        d_v[sl] = pf.v[sl] - pf.v[prim]

    pf.primary_range = p_r
    pf.d_range_to_primary = d_r
    pf.d_azimuth_to_primary = d_a
    pf.d_v_to_primary = d_v
