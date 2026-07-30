"""
micro_doppler/envelope.py -- foot-velocity envelope, paper Eq. (2).

    v_foot(t) = percentile_p( |v| : v in M(t) ),      p = 98

M(t) is a per-frame robust mask (median + k*MAD over that frame's VT column),
followed by light smoothing. The MAD is used rather than the standard deviation
because a single strong bulk-motion return would otherwise inflate the threshold
and mask out the very limb bins the envelope is meant to capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..core.maps import TimeMap


@dataclass
class Envelope:
    t: np.ndarray
    v: np.ndarray
    """v_foot(t), m/s."""
    mask_fraction: np.ndarray
    """Fraction of VT bins that survived the mask, per frame. A frame near 0
    contributed nothing; a frame near 1 means the mask failed to discriminate."""
    provenance: str

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.v)


def median_mad_mask(column: np.ndarray, k: float = 3.0) -> np.ndarray:
    """Robust per-frame mask: bins exceeding median + k*MAD.

    MAD is scaled by 1.4826 so k is interpretable in Gaussian-sigma units.
    """
    finite = column[np.isfinite(column)]
    if finite.size == 0:
        return np.zeros(column.shape, bool)
    med = np.median(finite)
    mad = np.median(np.abs(finite - med)) * 1.4826
    if mad <= 0:
        # Degenerate column (all equal): fall back to strictly-above-median.
        return np.isfinite(column) & (column > med)
    return np.isfinite(column) & (column > med + k * mad)


def foot_velocity_envelope(vt: TimeMap,
                           percentile: float = 98.0,
                           mad_k: float = 3.0,
                           smooth_frames: int = 3,
                           min_speed_mps: float = 0.0,
                           exclude_zero_bins: int = 1) -> Envelope:
    """Paper Eq. (2).

    exclude_zero_bins: drop |v| bins this close to zero before masking. With
    on-chip clutter removal disabled the zero-Doppler row dominates every
    column, and leaving it in pins the envelope near 0.
    """
    data = vt.data                      # (n_v, n_frames)
    v_axis = np.abs(vt.axis)
    n_v, n_f = data.shape

    usable = np.ones(n_v, bool)
    if exclude_zero_bins > 0:
        centre = int(np.argmin(np.abs(vt.axis)))
        lo = max(0, centre - exclude_zero_bins)
        hi = min(n_v, centre + exclude_zero_bins + 1)
        usable[lo:hi] = False
    if min_speed_mps > 0:
        usable &= v_axis >= min_speed_mps

    out = np.full(n_f, np.nan)
    frac = np.zeros(n_f)

    for i in range(n_f):
        col = np.where(usable, data[:, i], np.nan)
        m = median_mad_mask(col, k=mad_k)
        frac[i] = m.mean()
        if not m.any():
            continue
        out[i] = np.percentile(v_axis[m], percentile)

    if smooth_frames and smooth_frames > 1:
        out = _smooth_nanaware(out, smooth_frames)

    return Envelope(
        t=vt.t, v=out, mask_fraction=frac,
        provenance=("Eq.(2) percentile_%g(|v| in median+%.1f*MAD mask), "
                    "smoothing %d frames, zero-bin exclusion +-%d | source: %s"
                    % (percentile, mad_k, smooth_frames, exclude_zero_bins,
                       vt.provenance)))


def _smooth_nanaware(x: np.ndarray, w: int) -> np.ndarray:
    """Moving average that ignores NaN rather than propagating it."""
    if w < 2:
        return x
    k = np.ones(w)
    finite = np.isfinite(x)
    xv = np.where(finite, x, 0.0)
    num = np.convolve(xv, k, mode="same")
    den = np.convolve(finite.astype(float), k, mode="same")
    out = np.divide(num, den, out=np.full_like(num, np.nan), where=den > 0)
    out[~finite & (den == 0)] = np.nan
    return out


def envelope_from_points(t: np.ndarray, frame_idx: np.ndarray,
                         speed: np.ndarray, n_frames: int,
                         percentile: float = 98.0,
                         mad_k: float = 3.0,
                         smooth_frames: int = 3) -> Envelope:
    """Envelope computed directly from the point cloud.

    The tier-C path: instead of masking a dense VT column, take the per-frame
    set of detected |v| values and apply the same median+MAD mask and upper
    percentile. Fewer samples per frame, so noisier -- but it operates on
    exactly the quantity the device reported, with no binning step in between.
    """
    out = np.full(n_frames, np.nan)
    frac = np.zeros(n_frames)
    order = np.argsort(frame_idx, kind="stable")
    fi = frame_idx[order]
    sp = np.abs(speed[order])
    bounds = np.searchsorted(fi, np.arange(n_frames + 1))

    for i in range(n_frames):
        s = sp[bounds[i]:bounds[i + 1]]
        s = s[np.isfinite(s)]
        if s.size == 0:
            continue
        if s.size < 3:
            out[i] = s.max()
            frac[i] = 1.0
            continue
        m = median_mad_mask(s, k=mad_k)
        if not m.any():
            m = s >= np.median(s)
        frac[i] = m.mean()
        out[i] = np.percentile(s[m], percentile)

    if smooth_frames and smooth_frames > 1:
        out = _smooth_nanaware(out, smooth_frames)

    return Envelope(
        t=t, v=out, mask_fraction=frac,
        provenance=("Eq.(2) applied to the per-frame detected-point speed set "
                    "(percentile_%g, median+%.1f*MAD, smooth %d). SPARSE: "
                    "post-CFAR points, not a dense VT column."
                    % (percentile, mad_k, smooth_frames)))
