"""
micro_doppler/tracking.py -- target tracking through the RT map.

A direct port of `track_two_persons_from_RT` / `find_two_peaks` /
`fill_nan_interp` from the group's reference MATLAB
(`archive/reference_matlab/run_fixed_two_person.m`).

Why this matters far more than it looks: the reference VT is NOT a fixed-ROI
marginal. It is the Doppler spectrum taken at the range the target actually
occupies in that frame --

    b1  = nearest_bin(r_axis, track1_r(f));
    rr1 = b1-half_bins : b1+half_bins;          % +-0.25 m
    Z1  = sum(Xr(:,rr1), 2);
    D1  = fftshift(fft(Z1 .* w_d, nfft_d, 1));

A fixed ROI integrates every clutter source in the gate for the whole recording.
A following gate integrates the person. That single difference is most of why
the reference spectrograms are legible and a fixed-ROI VT is muddy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class Track:
    """One target's range over time. NaN means 'not held in this frame'."""

    range_m: np.ndarray
    t: np.ndarray
    label: str = "target"
    held: np.ndarray = None
    """True where the value was coasted rather than measured."""

    @property
    def valid_fraction(self) -> float:
        return float(np.isfinite(self.range_m).mean()) if self.range_m.size else 0.0


def find_peaks_in_profile(profile: np.ndarray, r_axis: np.ndarray,
                          n_peaks: int, min_sep_m: float) -> List[float]:
    """Greedy peak picking with a minimum separation.

    Port of `find_two_peaks`, generalised to N. Each peak taken in turn, then
    everything within `min_sep_m` of it zeroed so the next peak cannot be the
    same target's shoulder.
    """
    p = np.array(profile, dtype=float, copy=True)
    p[~np.isfinite(p)] = 0.0
    out: List[float] = []
    if not np.any(p > 0):
        return [np.nan] * n_peaks
    for _ in range(n_peaks):
        if not np.any(p > 0):
            out.append(np.nan)
            continue
        i = int(np.argmax(p))
        out.append(float(r_axis[i]))
        p[np.abs(r_axis - r_axis[i]) < min_sep_m] = 0.0
    return sorted(out, key=lambda v: (np.isnan(v), v))


def track_targets_from_rt(rt_lin: np.ndarray,
                          r_axis: np.ndarray,
                          t: np.ndarray,
                          n_targets: int = 1,
                          min_sep_m: float = 0.40,
                          max_jump_m: float = 0.60,
                          max_hold: int = 10,
                          roi_m: Optional[tuple] = None,
                          interpolate_gaps: bool = True) -> List[Track]:
    """Track `n_targets` through an RT map.

    rt_lin: (n_range, n_frames), linear or dB -- only ordering matters.

    Association is the reference's 2x2 minimum-total-cost rule generalised to a
    greedy nearest assignment. A candidate more than `max_jump_m` from the
    previous position is rejected and the track coasts for up to `max_hold`
    frames, matching the MATLAB. Coasting is what stops a track jumping onto a
    wall the moment the person is briefly occluded.
    """
    n_r, n_f = rt_lin.shape
    sel = np.ones(n_r, bool)
    if roi_m is not None:
        sel = (r_axis >= roi_m[0]) & (r_axis <= roi_m[1])
        if not sel.any():
            raise ValueError("tracking ROI %s contains no range bins" % (roi_m,))
    r_gate = r_axis[sel]
    gate = rt_lin[sel, :]

    tracks = np.full((n_targets, n_f), np.nan)
    held = np.zeros((n_targets, n_f), bool)

    first = find_peaks_in_profile(gate[:, 0], r_gate, n_targets, min_sep_m)
    prev = list(first)
    hold = [0] * n_targets
    tracks[:, 0] = first

    for f in range(1, n_f):
        cand = find_peaks_in_profile(gate[:, f], r_gate, n_targets, min_sep_m)
        cand = [c for c in cand if np.isfinite(c)]
        assigned = [np.nan] * n_targets
        used = set()

        # Greedy nearest assignment: cheapest (track, candidate) pair first.
        pairs = []
        for ti, pv in enumerate(prev):
            if not np.isfinite(pv):
                continue
            for ci, cv in enumerate(cand):
                pairs.append((abs(cv - pv), ti, ci))
        pairs.sort()
        taken_t = set()
        for cost, ti, ci in pairs:
            if ti in taken_t or ci in used:
                continue
            if cost > max_jump_m:
                continue
            assigned[ti] = cand[ci]
            taken_t.add(ti)
            used.add(ci)

        for ti in range(n_targets):
            if np.isfinite(assigned[ti]):
                tracks[ti, f] = assigned[ti]
                prev[ti] = assigned[ti]
                hold[ti] = 0
            else:
                hold[ti] += 1
                if hold[ti] <= max_hold and np.isfinite(prev[ti]):
                    tracks[ti, f] = prev[ti]
                    held[ti, f] = True

    out: List[Track] = []
    for ti in range(n_targets):
        r = tracks[ti]
        if interpolate_gaps:
            r = fill_nan_interp(r)
        out.append(Track(range_m=r, t=t,
                         label="target %d" % (ti + 1) if n_targets > 1 else "target",
                         held=held[ti]))
    return out


def fill_nan_interp(v: np.ndarray) -> np.ndarray:
    """Linear interpolation across NaN gaps, with edge extrapolation."""
    v = np.array(v, dtype=float, copy=True)
    ok = np.isfinite(v)
    if ok.sum() < 2:
        return v
    idx = np.arange(v.size)
    v[~ok] = np.interp(idx[~ok], idx[ok], v[ok])
    return v


def track_from_points(rec, n_targets: int = 1,
                      min_sep_m: float = 0.40,
                      max_jump_m: float = 0.60,
                      max_hold: int = 10,
                      min_abs_v: float = 0.0) -> List[Track]:
    """Track using the detected point cloud rather than the RT map.

    Useful at tier C, and better than RT tracking when the scene has strong
    static clutter: setting `min_abs_v` above 0 restricts tracking to MOVING
    points, so a wall cannot capture the track. The RT map has no such option
    because TI's range profile is a zero-Doppler slice dominated by clutter.
    """
    n_f = rec.num_frames
    per_frame_r: List[np.ndarray] = []
    per_frame_w: List[np.ndarray] = []
    for i, p in enumerate(rec.points):
        if p is None or p.size == 0:
            per_frame_r.append(np.zeros(0)); per_frame_w.append(np.zeros(0))
            continue
        r = np.sqrt(p["x"].astype(float) ** 2 + p["y"].astype(float) ** 2
                    + p["z"].astype(float) ** 2)
        v = p["doppler"].astype(float)
        w = np.ones(r.size)
        si = rec.side_info[i]
        if si is not None and si.size == r.size:
            w = 10.0 ** (si["snr"].astype(float) * 0.1 / 20.0)
        keep = np.abs(v) >= min_abs_v if min_abs_v > 0 else np.ones(r.size, bool)
        per_frame_r.append(r[keep]); per_frame_w.append(w[keep])

    tracks = np.full((n_targets, n_f), np.nan)
    prev = [np.nan] * n_targets
    hold = [0] * n_targets

    for f in range(n_f):
        r, w = per_frame_r[f], per_frame_w[f]
        cand: List[float] = []
        rr, ww = r.copy(), w.copy()
        for _ in range(n_targets):
            if rr.size == 0:
                break
            k = int(np.argmax(ww))
            cand.append(float(rr[k]))
            m = np.abs(rr - rr[k]) >= min_sep_m
            rr, ww = rr[m], ww[m]

        # (Re)seed whenever no track is currently alive. Without this, a first
        # frame with no qualifying points kills every track permanently: prev
        # stays NaN, the association generator below yields nothing, and the
        # coast branch also requires a finite prev. Re-seeding also lets a track
        # recover after a long occlusion instead of staying dead for the run.
        if cand and not any(np.isfinite(p) for p in prev):
            for ti, c in enumerate(sorted(cand)[:n_targets]):
                tracks[ti, f] = c
                prev[ti] = c
                hold[ti] = 0
            continue
        if not cand:
            for ti in range(n_targets):
                if np.isfinite(prev[ti]):
                    hold[ti] += 1
                    if hold[ti] <= max_hold:
                        tracks[ti, f] = prev[ti]
            continue

        pairs = sorted((abs(c - prev[ti]), ti, ci)
                       for ti in range(n_targets) if np.isfinite(prev[ti])
                       for ci, c in enumerate(cand))
        taken_t, used = set(), set()
        for cost, ti, ci in pairs:
            if ti in taken_t or ci in used or cost > max_jump_m:
                continue
            tracks[ti, f] = cand[ci]
            prev[ti] = cand[ci]
            hold[ti] = 0
            taken_t.add(ti); used.add(ci)
        for ti in range(n_targets):
            if ti not in taken_t:
                hold[ti] += 1
                if hold[ti] <= max_hold and np.isfinite(prev[ti]):
                    tracks[ti, f] = prev[ti]
                elif hold[ti] > max_hold:
                    # Track DELETION. Without this a track that seeds onto a
                    # spurious return sits there forever: every real candidate
                    # is further than max_jump_m so it is rejected, prev stays
                    # finite so the re-seed branch never fires, and the whole
                    # run interpolates a straight line through a wall.
                    prev[ti] = np.nan
                    hold[ti] = 0

    out = []
    for ti in range(n_targets):
        raw = tracks[ti]
        valid = float(np.isfinite(raw).mean())
        if valid == 0.0:
            print("   WARNING: target %d was never acquired -- no frame had a "
                  "point with |v| >= %.2f m/s and a peak above threshold. "
                  "Track is unusable; downstream target-following maps will be "
                  "empty." % (ti + 1, min_abs_v))
        elif valid < 0.25:
            print("   WARNING: target %d held in only %.0f%% of frames; the "
                  "track is mostly interpolated." % (ti + 1, 100 * valid))
        out.append(Track(range_m=fill_nan_interp(raw), t=rec.t,
                         label="target %d" % (ti + 1) if n_targets > 1 else "target",
                         held=~np.isfinite(raw)))
    return out
