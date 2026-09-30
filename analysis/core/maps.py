"""
core/maps.py -- Range-Time (RT) and Velocity-Time (VT) maps.

The reference paper (Ganditi et al., Eq. 1) defines, after clutter suppression
and range gating to an ROI R:

    RT(r, t) = sum_v  RD'(r, v, t),   r in R
    VT(v, t) = sum_{r in R} RD'(r, v, t)

Both are marginals of the range-Doppler map. Whether we can compute them
faithfully depends entirely on what the capture actually contains:

  TIER B  RD(r,v,t) present (TLV 5)  -> Eq. (1) verbatim. Dense, correct.
  TIER C+ range profile present      -> RT is dense and real (TLV 2 is a
          measured range spectrum, not a marginal, so it is a close proxy);
          VT must come from the sparse point cloud.
  TIER C  point cloud only           -> both are SPARSE reconstructions.

A sparse VT is not the paper's VT. The point cloud has already been through
CFAR, peak grouping and FoV gating on the radar DSP, so weak micro-Doppler
returns that a dense spectrogram would show are simply absent. Every function
here reports which tier produced its output so that never gets forgotten in a
figure caption.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from .recording import Recording


@dataclass
class TimeMap:
    """A 2-D map over (feature axis, time)."""

    data: np.ndarray          # (n_axis, n_frames)
    axis: np.ndarray          # physical value of each row
    t: np.ndarray             # seconds
    axis_label: str
    value_label: str
    tier: str
    provenance: str
    """Human-readable statement of exactly how this was built."""

    @property
    def shape(self):
        return self.data.shape

    def db(self, floor_db: float = -120.0) -> "TimeMap":
        """Convert a linear map to dB. Already-dB maps pass through."""
        if self.value_label.endswith("dB"):
            return self
        d = 20.0 * np.log10(np.maximum(self.data, 1e-12))
        d = np.maximum(d, floor_db)
        return TimeMap(d, self.axis, self.t, self.axis_label, "power (dB)",
                       self.tier, self.provenance)

    def clip_percentile(self, lo: float = 35.0, hi: float = 99.5
                        ) -> Tuple[float, float]:
        """Display limits, matching the reference MATLAB (prctile 35..99.5)."""
        f = self.data[np.isfinite(self.data)]
        if f.size == 0:
            return (0.0, 1.0)
        return (float(np.percentile(f, lo)), float(np.percentile(f, hi)))


# ---------------------------------------------------------------------------
# Range-Time
# ---------------------------------------------------------------------------


def range_time_map(rec: Recording,
                   roi_m: Optional[Tuple[float, float]] = None,
                   prefer: str = "auto") -> TimeMap:
    """RT(r, t).

    prefer: 'auto' | 'rd' | 'profile' | 'points'
    """
    g = rec.geom
    r_axis = g.range_axis()

    use = prefer
    if use == "auto":
        use = ("rd" if rec.rd_map is not None
               else "profile" if rec.range_profile is not None
               else "points")

    if use == "rd":
        if rec.rd_map is None:
            raise ValueError("no range-Doppler map in this recording")
        # Eq. (1): marginalise the RD map over Doppler.
        data = np.nansum(rec.rd_map, axis=1).T            # (range, frames)
        prov = ("Eq.(1) RT(r,t)=sum_v RD'(r,v,t) over the TLV-5 "
                "range-Doppler map")
        val = "power (linear)"

    elif use == "profile":
        if rec.range_profile is None:
            raise ValueError("no range profile (TLV 2) in this recording")
        # TLV 2 is the measured log-magnitude range spectrum, already in dB.
        data = rec.range_profile.T
        prov = ("dense range profile (TLV 2), TI Q-format -> dB. Not a "
                "Doppler marginal: it is the measured range spectrum.")
        val = "relative power (dB)"

    else:
        data, prov = _sparse_range_time(rec)
        val = "point density"

    m = TimeMap(data, r_axis, rec.t, "Range (m)", val, rec.tier, prov)
    if roi_m is not None:
        m = gate_range(m, roi_m)
    return m


def _sparse_range_time(rec: Recording):
    """Accumulate detected points into range bins -- the fallback."""
    g = rec.geom
    n_r, n_f = g.num_range_bins, rec.num_frames
    data = np.zeros((n_r, n_f))
    for i, p in enumerate(rec.points):
        if p is None or p.size == 0:
            continue
        r = np.sqrt(p["x"].astype(float) ** 2 + p["y"].astype(float) ** 2
                    + p["z"].astype(float) ** 2)
        idx = np.clip(np.round(r / g.range_idx_to_meters).astype(int), 0, n_r - 1)
        w = np.ones(idx.size)
        si = rec.side_info[i]
        if si is not None and si.size == idx.size:
            # weight by linear SNR so strong returns dominate, as an intensity
            # map would
            w = 10.0 ** (si["snr"].astype(float) * 0.1 / 20.0)
        np.add.at(data, (idx, np.full(idx.size, i)), w)
    return data, ("SPARSE: detected points binned by range. Post-CFAR, so "
                  "weak returns are absent by construction.")


# ---------------------------------------------------------------------------
# Velocity-Time
# ---------------------------------------------------------------------------


def velocity_time_map(rec: Recording,
                      roi_m: Optional[Tuple[float, float]] = None,
                      prefer: str = "auto",
                      keep_mask: Optional[np.ndarray] = None) -> TimeMap:
    """VT(v, t).

    `keep_mask` is a per-point boolean from a ghost method; None means raw.
    A point-cloud decision cannot filter the measured TLV-5 spectrum, so a
    mask forces the sparse points path (same honesty rule as
    sparse_range_time_masked).
    """
    g = rec.geom
    v_axis = g.velocity_axis()

    use = prefer
    if keep_mask is not None:
        use = "points"
    elif use == "auto":
        use = "rd" if rec.rd_map is not None else "points"

    if use == "rd":
        if rec.rd_map is None:
            raise ValueError("no range-Doppler map in this recording")
        cube = rec.rd_map                                  # (f, dop, range)
        if roi_m is not None:
            r_axis = g.range_axis()
            sel = (r_axis >= roi_m[0]) & (r_axis <= roi_m[1])
            cube = cube[:, :, sel]
        data = np.nansum(cube, axis=2).T                   # (dop, frames)
        prov = ("Eq.(1) VT(v,t)=sum_{r in ROI} RD'(r,v,t) over the TLV-5 "
                "range-Doppler map")
        val = "power (linear)"
    else:
        data, prov = _sparse_velocity_time(rec, roi_m, keep_mask)
        val = "point density"

    return TimeMap(data, v_axis, rec.t, "Velocity (m/s)", val,
                   rec.tier, prov)


def _sparse_velocity_time(rec: Recording,
                          roi_m: Optional[Tuple[float, float]],
                          keep_mask: Optional[np.ndarray] = None):
    g = rec.geom
    n_v, n_f = g.num_doppler_bins, rec.num_frames
    data = np.zeros((n_v, n_f))
    half = n_v // 2
    off = 0
    for i, p in enumerate(rec.points):
        n_pts = 0 if p is None else p.size
        if n_pts == 0:
            continue
        sl = slice(off, off + n_pts)
        off += n_pts
        r = np.sqrt(p["x"].astype(float) ** 2 + p["y"].astype(float) ** 2
                    + p["z"].astype(float) ** 2)
        v = p["doppler"].astype(float)
        keep = np.ones(v.size, bool)
        if roi_m is not None:
            keep = (r >= roi_m[0]) & (r <= roi_m[1])
        if keep_mask is not None and keep_mask.size >= off:
            keep &= keep_mask[sl]
        if not keep.any():
            continue
        idx = np.clip(np.round(v[keep] / g.doppler_resolution_mps).astype(int)
                      + half, 0, n_v - 1)
        w = np.ones(idx.size)
        si = rec.side_info[i]
        if si is not None and si.size == v.size:
            w = 10.0 ** (si["snr"].astype(float)[keep] * 0.1 / 20.0)
        np.add.at(data, (idx, np.full(idx.size, i)), w)
    return data, ("SPARSE: detected points binned by Doppler. This is NOT the "
                  "paper's dense VT -- CFAR removed sub-threshold "
                  "micro-Doppler before the host saw it.")


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def velocity_time_following(rec: Recording,
                            track,
                            half_width_m: float = 0.25,
                            keep_mask: Optional[np.ndarray] = None) -> TimeMap:
    """VT with a range gate that FOLLOWS the target -- the reference method.

    `run_fixed_two_person.m` does not marginalise a fixed ROI. Per frame it
    takes a +-0.25 m window around the tracked range and Doppler-transforms only
    that (`dyn_gate_halfwidth_m`, `rr1 = b1-half_bins : b1+half_bins`).

    A fixed ROI integrates every clutter source in the gate for the entire
    recording; a following gate integrates the person. This is the single
    biggest reason the reference VT spectrograms are legible.

    At tier B the window is applied to the RD map. At tier C it selects points,
    which is sparser but preserves the same idea.
    """
    g = rec.geom
    v_axis = g.velocity_axis()
    n_v, n_f = g.num_doppler_bins, rec.num_frames
    r_track = np.asarray(track.range_m, dtype=float)

    # A dead track would otherwise produce a plausible-looking all-zero map and
    # a silent zero step count. Fail loudly instead.
    if not np.isfinite(r_track).any():
        raise ValueError(
            "target-following VT requires a usable track, but this one has no "
            "finite range values. Lower min_abs_v in track_from_points(), or "
            "use velocity_time_map() for a fixed-ROI VT instead.")

    if rec.rd_map is not None:
        r_axis = g.range_axis()
        data = np.zeros((n_v, n_f))
        for i in range(min(n_f, rec.rd_map.shape[0])):
            if not np.isfinite(r_track[i]):
                continue
            sel = np.abs(r_axis - r_track[i]) <= half_width_m
            if not sel.any():
                continue
            data[:, i] = np.nansum(rec.rd_map[i][:, sel], axis=1)
        prov = ("target-following VT: RD map summed over a +-%.2f m window "
                "around the track (reference dyn_gate_halfwidth_m)"
                % half_width_m)
        val = "power (linear)"
    else:
        data = np.zeros((n_v, n_f))
        half = n_v // 2
        off = 0
        for i, p in enumerate(rec.points):
            n_pts = 0 if p is None else p.size
            if n_pts == 0:
                continue
            sl = slice(off, off + n_pts)
            off += n_pts
            if not np.isfinite(r_track[i]):
                continue
            r = np.sqrt(p["x"].astype(float) ** 2 + p["y"].astype(float) ** 2
                        + p["z"].astype(float) ** 2)
            v = p["doppler"].astype(float)
            keep = np.abs(r - r_track[i]) <= half_width_m
            if keep_mask is not None and keep_mask.size >= off:
                keep &= keep_mask[sl]
            if not keep.any():
                continue
            idx = np.clip(np.round(v[keep] / g.doppler_resolution_mps).astype(int)
                          + half, 0, n_v - 1)
            w = np.ones(idx.size)
            si = rec.side_info[i]
            if si is not None and si.size == n_pts:
                w = 10.0 ** (si["snr"].astype(float)[keep] * 0.1 / 20.0)
            np.add.at(data, (idx, np.full(idx.size, i)), w)
        prov = ("target-following VT: detected points within +-%.2f m of the "
                "track, binned by Doppler. SPARSE (post-CFAR)." % half_width_m)
        val = "point density"

    return TimeMap(data, v_axis, rec.t, "Velocity (m/s)", val, rec.tier, prov)


def sparse_range_time_masked(rec: Recording,
                             keep_mask: Optional[np.ndarray] = None) -> TimeMap:
    """RT built ONLY from the point cloud, honouring a ghost mask.

    The dense TLV-2 range profile is a measured spectrum and cannot be filtered
    by a point-cloud decision -- so a "filtered" RT drawn from it is identical to
    the unfiltered one. This builds RT from the points instead, so a ghost method
    visibly changes it. Sparser than the dense map, but honest.
    """
    g = rec.geom
    n_r, n_f = g.num_range_bins, rec.num_frames
    data = np.zeros((n_r, n_f))
    off = 0
    for i, p in enumerate(rec.points):
        n_pts = 0 if p is None else p.size
        if n_pts == 0:
            continue
        sl = slice(off, off + n_pts)
        off += n_pts
        r = np.sqrt(p["x"].astype(float) ** 2 + p["y"].astype(float) ** 2
                    + p["z"].astype(float) ** 2)
        keep = np.ones(n_pts, bool)
        if keep_mask is not None and keep_mask.size >= off:
            keep = keep_mask[sl]
        if not keep.any():
            continue
        idx = np.clip(np.round(r[keep] / g.range_idx_to_meters).astype(int),
                      0, n_r - 1)
        w = np.ones(idx.size)
        si = rec.side_info[i]
        if si is not None and si.size == n_pts:
            w = 10.0 ** (si["snr"].astype(float)[keep] * 0.1 / 20.0)
        np.add.at(data, (idx, np.full(idx.size, i)), w)
    tag = "" if keep_mask is None else " (ghost-filtered)"
    return TimeMap(data, g.range_axis(), rec.t, "Range (m)", "point density",
                   rec.tier,
                   "SPARSE RT from detected points%s -- responds to point-level "
                   "filtering, unlike the dense TLV-2 profile" % tag)


def gate_range(m: TimeMap, roi_m: Tuple[float, float]) -> TimeMap:
    """Restrict an RT map to a range ROI (paper §2.3 range gating)."""
    sel = (m.axis >= roi_m[0]) & (m.axis <= roi_m[1])
    if not sel.any():
        raise ValueError("range ROI %s contains no bins (axis spans %.2f..%.2f m)"
                         % (roi_m, m.axis[0], m.axis[-1]))
    return TimeMap(m.data[sel], m.axis[sel], m.t, m.axis_label, m.value_label,
                   m.tier, m.provenance + " | range-gated to %.2f..%.2f m"
                   % roi_m)
