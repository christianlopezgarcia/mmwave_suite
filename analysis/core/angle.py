"""
core/angle.py -- host-side beamforming from the TLV 4/8 complex I/Q.

Why this module is the important one for multipath work
------------------------------------------------------
Every other payload the device sends has already been through its FFTs. The
azimuth heat map has not: it is raw complex samples, one per virtual antenna,
per range bin, at zero Doppler. The angle transform is left to the host.

That matters because a specular ghost is *defined* by arriving from the wrong
direction. Range and Doppler cannot distinguish it -- a mirror image sits at a
different bearing, and bearing is the one axis you can still process yourself.

    RANGE-AZIMUTH map    one frame -> a Cartesian picture of the room
    AZIMUTH-TIME map     dense angle-vs-time -- the direct analogue of the
                         sparse azimuth_time_intensity point-cloud figure, but
                         computed from signal rather than from detections

Method: zero-pad the antenna axis to NUM_ANGLE_BINS and FFT it. Angle bin k maps
to sin(theta) = 2k/N, i.e. theta = asin(2k/N) -- so the angular axis is NOT
linear, it is compressed toward broadside. This mirrors TI's own processing
(mmWave.js:2967-2983) so results are comparable with the Visualizer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from .maps import TimeMap
from .recording import Recording

# mmWave.js:1800
NUM_ANGLE_BINS = 64


def angle_axis_deg(num_angle_bins: int = NUM_ANGLE_BINS) -> np.ndarray:
    """Bearing of each angle bin, degrees.

    mmWave.js:3251: theta = asin(k * 2/N) for k in -N/2+1 .. N/2-1, giving
    N-1 bins. Non-uniform by construction -- resolution is finest at broadside
    and degrades toward the edges, which is a property of a linear array, not a
    choice.
    """
    k = np.arange(-num_angle_bins // 2 + 1, num_angle_bins // 2)
    return np.degrees(np.arcsin(np.clip(k * (2.0 / num_angle_bins), -1, 1)))


def _beamform(iq: np.ndarray, num_angle_bins: int = NUM_ANGLE_BINS) -> np.ndarray:
    """(range, ant) complex -> (range, num_angle_bins-1) magnitude.

    Zero-pad, FFT, magnitude, fftshift, then drop the first column and reverse
    so bearing increases left-to-right. Exactly TI's chain at
    mmWave.js:2967-2978; matching it means a figure here can be checked against
    the Visualizer's own heat map.
    """
    n_r, n_ant = iq.shape
    padded = np.zeros((n_r, num_angle_bins), dtype=np.complex128)
    padded[:, :min(n_ant, num_angle_bins)] = iq[:, :num_angle_bins]
    Q = np.abs(np.fft.fft(padded, n=num_angle_bins, axis=1))
    Q = np.roll(Q, num_angle_bins // 2, axis=1)
    return Q[:, 1:][:, ::-1]


@dataclass
class RangeAzimuth:
    """One frame's range-azimuth magnitude map."""

    data: np.ndarray            # (range_bins, angle_bins)
    range_axis: np.ndarray      # metres
    angle_axis: np.ndarray      # degrees
    frame_index: int

    def to_cartesian(self, grid: int = 128,
                     x_half_m: Optional[float] = None,
                     y_max_m: Optional[float] = None):
        """Resample onto a Cartesian grid, as the Visualizer's plot4 does.

        Returns (x_lin, y_lin, Z). Requires scipy; without it, plot the polar
        form directly instead.
        """
        from scipy.interpolate import griddata
        th = np.radians(self.angle_axis)
        r = self.range_axis
        X = np.outer(r, np.sin(th)).ravel()
        Y = np.outer(r, np.cos(th)).ravel()
        xh = x_half_m if x_half_m is not None else float(np.nanmax(r))
        ym = y_max_m if y_max_m is not None else float(np.nanmax(r))
        xl = np.linspace(-xh, xh, grid)
        yl = np.linspace(0.0, ym, grid)
        XI, YI = np.meshgrid(xl, yl)
        Z = griddata((X, Y), self.data.ravel(), (XI, YI), method="linear")
        return xl, yl, Z


def range_azimuth(rec: Recording, frame_index: int,
                  num_angle_bins: int = NUM_ANGLE_BINS) -> RangeAzimuth:
    """Beamform one frame."""
    if rec.angle_iq is None:
        raise ValueError(
            "this recording has no angle I/Q. Capture with a config whose "
            "guiMonitor enables rangeAzimuthHeatMap (e.g. angle-ghost), which "
            "emits TLV %d." % (rec.angle_tlv or 8))
    if not (0 <= frame_index < rec.angle_iq.shape[0]):
        raise IndexError("frame %d out of range (0..%d)"
                         % (frame_index, rec.angle_iq.shape[0] - 1))
    mag = _beamform(rec.angle_iq[frame_index], num_angle_bins)
    return RangeAzimuth(mag, rec.geom.range_axis(),
                        angle_axis_deg(num_angle_bins), frame_index)


def azimuth_time_map(rec: Recording,
                     roi_m: Optional[Tuple[float, float]] = None,
                     num_angle_bins: int = NUM_ANGLE_BINS,
                     track=None,
                     half_width_m: float = 0.5) -> TimeMap:
    """Dense azimuth vs time -- integrate the beamformed map over range.

    The signal-domain counterpart of the sparse `azimuth_time_intensity` point
    figure. Because it is built from signal rather than detections, a weak
    specular image that never passed CFAR still appears here.

    Pass `track` to follow the target instead of using a fixed ROI; a following
    gate keeps the wall's own bearing out of the integration.
    """
    if rec.angle_iq is None:
        raise ValueError(
            "this recording has no angle I/Q (needs TLV 4/8). Use the "
            "point-cloud azimuth figure instead.")
    g = rec.geom
    r_axis = g.range_axis()
    n_f = rec.angle_iq.shape[0]
    ang = angle_axis_deg(num_angle_bins)
    out = np.zeros((ang.size, n_f))

    r_track = (np.asarray(track.range_m, dtype=float)
               if track is not None else None)

    for i in range(n_f):
        if r_track is not None:
            if not np.isfinite(r_track[i]):
                continue
            sel = np.abs(r_axis - r_track[i]) <= half_width_m
        elif roi_m is not None:
            sel = (r_axis >= roi_m[0]) & (r_axis <= roi_m[1])
        else:
            sel = np.ones(r_axis.size, bool)
        if not sel.any():
            continue
        mag = _beamform(rec.angle_iq[i], num_angle_bins)
        k = min(mag.shape[0], sel.size)
        out[:, i] = mag[:k][sel[:k]].sum(axis=0)

    if track is not None:
        prov = ("dense azimuth-time from TLV %d angle I/Q, host beamformed, "
                "integrated over a +-%.2f m window following the track"
                % (rec.angle_tlv or 8, half_width_m))
    else:
        prov = ("dense azimuth-time from TLV %d angle I/Q, host beamformed, "
                "integrated over %s" % (rec.angle_tlv or 8,
                                        "%.2f-%.2f m" % roi_m if roi_m else "all range"))
    return TimeMap(out, ang, rec.t, "Azimuth (deg)", "power (linear)",
                   rec.tier, prov)


def angular_spread(rec: Recording, frame_index: int,
                   range_m: float, half_width_m: float = 0.3,
                   num_angle_bins: int = NUM_ANGLE_BINS):
    """Beam pattern at one range, in one frame.

    Two peaks at the same range is the cleanest possible ghost evidence from a
    single frame: one scatterer cannot occupy two bearings.
    """
    ra = range_azimuth(rec, frame_index, num_angle_bins)
    sel = np.abs(ra.range_axis - range_m) <= half_width_m
    if not sel.any():
        raise ValueError("no range bins within +-%.2f m of %.2f m"
                         % (half_width_m, range_m))
    return ra.angle_axis, ra.data[sel].sum(axis=0)
