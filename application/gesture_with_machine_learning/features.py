"""Gesture features: TI's exact definitions, and host-computable stand-ins.

Three extractors live here, in descending order of fidelity to the board:

  `rdi_features` + `angle_features_from_cube`
        Exact ports of gesture.c.  They need the LINEAR-magnitude detection
        matrix and the per-antenna 2D-FFT cube, neither of which stock
        out-of-box firmware puts on the UART.  Use these when you have the
        cube -- a DCA1000 raw capture, or custom firmware -- or to check a
        host extractor against ground truth.

  `HeatmapFeatureExtractor`
        Same feature definitions, driven from TLV 5 (the range-Doppler heat
        map) that the out-of-box demo can emit.  Gets five of the six ANN
        inputs.  The two angle features are NOT recoverable from TLV 5 and
        are supplied from the point cloud instead.

  `PointCloudFeatureExtractor`
        All six features from TLV 1 + TLV 7 alone, at full frame rate and a
        tenth of the bandwidth.  Analogous, not identical -- see the module
        note below.

WHY THE TI MODEL DOES NOT TRANSFER TO THE LAST TWO
--------------------------------------------------
The gesture firmware ships a *modified* Doppler DPU.  Two changes matter:

  * dopplerprochwa.c:300 sets HWA_FFT_MODE_MAGNITUDE_ONLY_ENABLED and writes
    uint32.  The stock SDK DPU (SDK 3.6 dopplerprochwa.c:197) sets
    MAGNITUDE_LOG2_ENABLED and writes uint16.  So the gesture detection
    matrix holds LINEAR magnitude and TLV 5 holds LOG2 magnitude.
  * dopplerprochwa.c:324, "Only accumulate on TX1-RX1 for the detection
    matrix" -- the gesture matrix is one virtual channel.  TLV 5 is summed
    over all twelve.

Every magnitude-weighted feature therefore lands on a different scale, and
`numDetections` compares against a threshold of 2000 on a quantity that no
longer exists.  A log-sum over 12 antennas is not an invertible function of a
linear magnitude on 1, so there is no correction factor that fixes this.
`model/ti_ann_6843.npz` is only valid on features from TI's own firmware.
For anything else, record features with `dataset.py` and retrain with
`train.py`; the architecture and the pipeline carry over unchanged, only the
weights do not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

import numpy as np

from .ann import ANN_FEATURE_ORDER, FEATURE_LEN, NUM_FEATURES

# gesture.h:81-88
NUM_ANGLE_BINS = 32
THRESH_NUM_POINTS = 2000
NUM_SORTED_VALUES = 10
LEN_CORR = 20
DOPPLER_BINS_TO_SUPPRESS = 5
RANGE_BIN_START = 1
RANGE_BIN_END = 8

# Order of the ten floats in TLV 1050, i.e. the first ten members of
# Features_t (gesture.h:145-154).  Only six of them feed the network.
TLV_FEATURE_ORDER = (
    "weightedDoppler",
    "weightedPositiveDoppler",
    "weightedNegativeDoppler",
    "weightedRange",
    "numDetections",
    "weightedAzimuthMean",
    "weightedElevationMean",
    "azimuthDopplerCorr",
    "weightedAzimuthDispersion",
    "weightedElevationDispersion",
)


def doppler_weights(n_doppler: int) -> np.ndarray:
    """Signed Doppler bin index: 0..N/2-1 then -N/2..-1.

    gesture.c:64-191 spells this out as a 128-entry int8 table.  Note this is
    an unshifted FFT output ordering, so bin 0 is DC and the negative
    velocities live in the upper half.
    """
    w = np.arange(n_doppler, dtype=np.int32)
    w[n_doppler // 2:] -= n_doppler
    return w


def suppress_zero_doppler(det: np.ndarray,
                          n_suppress: int = DOPPLER_BINS_TO_SUPPRESS,
                          copy: bool = True) -> np.ndarray:
    """Zero bins 0..n and N-n..N-1 across every range bin.

    gesture.c:206-240.  With TI's 128-bin, 200 us chirp this discards
    everything slower than 0.47 m/s -- static clutter, but also the slow part
    of a gentle swipe.  It is why the demo needs a brisk gesture.

    `det` is indexed [range][doppler].
    """
    out = det.astype(np.float64, copy=copy)
    n_dopp = out.shape[1]
    out[:, :n_suppress + 1] = 0.0
    out[:, n_dopp - n_suppress:] = 0.0
    return out


@dataclass
class RdiFeatures:
    weighted_doppler: float = 0.0
    weighted_doppler_pos: float = 0.0
    weighted_doppler_neg: float = 0.0
    weighted_range: float = 0.0
    num_detections: float = 0.0
    inst_energy: float = 0.0


def rdi_features(det: np.ndarray,
                 range_start: int = RANGE_BIN_START,
                 range_end: int = RANGE_BIN_END,
                 threshold: float = THRESH_NUM_POINTS) -> RdiFeatures:
    """Magnitude-weighted range/Doppler moments over a range window.

    Exact port of `Computefeatures_RDIBased` (gesture.c:242-317).  `det` is
    [range][doppler], linear magnitude, already zero-Doppler suppressed.

    Two details that are easy to get wrong and both matter:
      * the range window is [range_start, range_end), i.e. bins 1..7 -- bin 0
        is excluded from the moments but IS searched for the angle peak;
      * "positive" Doppler is bins [0, N/2) and "negative" is [N/2, N), the
        raw FFT halves, so the positive sum includes DC.  After suppression
        DC is zero anyway, which is presumably why TI never fixed it.
    """
    f = RdiFeatures()
    n_dopp = det.shape[1]
    w = det[range_start:range_end, :].astype(np.float64)
    if w.size == 0:
        return f

    dw = doppler_weights(n_dopp).astype(np.float64)
    r_idx = np.arange(range_start, range_end, dtype=np.float64)[:, None]

    wt_sum = float(w.sum())
    f.inst_energy = wt_sum / 10000.0
    f.num_detections = float((w > threshold).sum())

    if wt_sum > 0:
        f.weighted_range = float((w * r_idx).sum() / wt_sum)
        f.weighted_doppler = float((w * dw[None, :]).sum() / wt_sum)

    half = n_dopp // 2
    pos, neg = w[:, :half], w[:, half:]
    s_pos, s_neg = float(pos.sum()), float(neg.sum())
    if s_pos > 0:
        f.weighted_doppler_pos = float((pos * dw[None, :half]).sum() / s_pos)
    if s_neg > 0:
        f.weighted_doppler_neg = float((neg * dw[None, half:]).sum() / s_neg)
    return f


# ---------------------------------------------------------------------------
# Angle features -- require the per-antenna 2D-FFT cube
# ---------------------------------------------------------------------------


def aop_doa_input(cell: np.ndarray) -> np.ndarray:
    """Four virtual-antenna samples -> the 8-element vector the HWA is fed.

    gesture.c:430-495, the MMW_6843_AOP branch.  `cell` is the 2D-FFT output
    at one (range, Doppler) cell for TX1-RX1..RX4 in that order.

    The reversal and the two sign flips are antenna-layout facts about the
    6843 AOP module (two of the RX feeds enter from the opposite side, so
    their virtual channels are 180 degrees out).  The trailing four zeros are
    TX2's channels, which this single-TX config never populates; the HWA can
    zero-pad but not zero-fill, hence the explicit zeros.
    """
    c = np.asarray(cell, dtype=np.complex128).reshape(-1)
    if c.size < 4:
        raise ValueError("need 4 virtual-antenna samples, got %d" % c.size)
    out = np.zeros(8, dtype=np.complex128)
    out[3], out[2], out[1], out[0] = c[0], c[1], c[2], c[3]
    out[0] *= -1.0      # TX1-RX4 in this ordering
    out[2] *= -1.0      # TX1-RX2
    return out / 64.0   # gesture.c scales the 2D-FFT input down by 2^6


def angle_fft_2d(doa_input: np.ndarray, n_bins: int = NUM_ANGLE_BINS,
                 n_virt: int = 4) -> np.ndarray:
    """The HWA's two chained FFTs, as one (n_bins, n_bins) magnitude array.

    dopplerprochwa.c:509-620.  First paramset: `n_virt` separate 2-point FFTs
    over adjacent pairs of the input, each zero-padded to 32.  Second: for
    each of the 32 output bins, one 4-point FFT across the pair results, also
    zero-padded to 32, magnitude only.

    The returned array is indexed [a][b] exactly as the HWA writes it
    (dstAIdx = 32 uint32, dstBIdx = 1), which is what `ComputeAngleStats`
    scans linearly.  That routine calls a//32 the azimuth index and a%32 the
    elevation index; whether that labelling matches the physical axes of the
    AOP patch is not determinable from the source -- the paramset was
    "modified so the angle matrix matches the MATLAB model"
    (dopplerprochwa.c:1423) and the swap that used to be here is commented
    out.  It is one left-swipe capture to settle empirically, and it only
    flips two feature columns if it is wrong.
    """
    x = np.asarray(doa_input, dtype=np.complex128).reshape(-1)
    pairs = x.reshape(-1, 2)[:n_virt]              # (n_virt, 2)
    stage1 = np.fft.fft(pairs, n=n_bins, axis=1)   # (n_virt, 32)
    stage2 = np.fft.fft(stage1, n=n_bins, axis=0)  # (32, 32) over pairs
    return np.abs(stage2)


@dataclass
class AngleFeatures:
    az_mean: float = 0.0
    el_mean: float = 0.0
    az_dispersion: float = 0.0
    el_dispersion: float = 0.0


def angle_features_from_cube(det: np.ndarray,
                             cube: np.ndarray,
                             n_sorted: int = NUM_SORTED_VALUES,
                             range_end: int = RANGE_BIN_END) -> AngleFeatures:
    """Weighted angle moments over the `n_sorted` strongest cells.

    Port of the DoA loop in dopplerprochwa.c:1311-1440.  `det` is
    [range][doppler] linear magnitude (zero-Doppler already suppressed);
    `cube` is [range][doppler][virtual antenna] complex.

    Each iteration takes the current global maximum inside range bins
    [0, range_end), angle-FFTs that one cell, records the peak angle bin
    weighted by the cell's magnitude, then ZEROES the cell so the next
    iteration finds the next-strongest.  It is a fixed-count detector, which
    is the quiet reason this works better than CFAR for gestures: the feature
    vector has the same statistical weight every frame whether the hand is
    near or far, present or absent.

    Dispersion is E[i^2] - E[i]^2 on bin indices (dopplerprochwa.c:1423-1427)
    -- a variance, despite `pWtaz_std` being named like a standard deviation.
    """
    work = det.astype(np.float64, copy=True)
    work[range_end:, :] = 0.0

    wt_sum = 0.0
    az_sum = el_sum = az_sq = el_sq = 0.0
    for _ in range(n_sorted):
        flat = int(np.argmax(work))
        r, d = np.unravel_index(flat, work.shape)
        weight = float(work[r, d])

        mag = angle_fft_2d(aop_doa_input(cube[r, d, :]))
        a, b = np.unravel_index(int(np.argmax(mag)), mag.shape)
        # Wrap to a signed bin index, gesture.c:524-532.
        az = a - NUM_ANGLE_BINS if a > NUM_ANGLE_BINS // 2 - 1 else a
        el = b - NUM_ANGLE_BINS if b > NUM_ANGLE_BINS // 2 - 1 else b

        wt_sum += weight
        az_sum += az * weight
        el_sum += el * weight
        az_sq += az * az * weight
        el_sq += el * el * weight
        work[r, d] = 0.0

    if wt_sum <= 0:
        return AngleFeatures()
    az_m, el_m = az_sum / wt_sum, el_sum / wt_sum
    return AngleFeatures(az_m, el_m, az_sq / wt_sum - az_m * az_m,
                         el_sq / wt_sum - el_m * el_m)


# ---------------------------------------------------------------------------
# The sliding window and the correlation buffer
# ---------------------------------------------------------------------------


class FeatureWindow:
    """15-frame normalised window + the 20-frame azimuth/Doppler correlation.

    `azimuth_doppler_correlation` is the one feature with memory longer than
    the ANN window: gesture.c:540-573 correlates the last 20 frames of
    weighted azimuth against weighted Doppler.  That is what separates a
    twirl (azimuth and Doppler in quadrature, so near-zero correlation over a
    full circle) from a swipe (azimuth and Doppler locked in phase, so
    strongly signed).  Before 20 frames have accumulated the C code reports
    0, and so does this.
    """

    def __init__(self, ann, feature_len: int = FEATURE_LEN,
                 len_corr: int = LEN_CORR):
        self.ann = ann
        self.feature_len = feature_len
        self.len_corr = len_corr
        self.reset()

    def reset(self) -> None:
        self.window = np.zeros((self.feature_len, NUM_FEATURES), dtype=np.float64)
        self.az_buf = np.zeros(self.len_corr, dtype=np.float64)
        self.dopp_buf = np.zeros(self.len_corr, dtype=np.float64)
        self.frame_count = 0

    def correlation(self, az_mean: float, weighted_doppler: float) -> float:
        """Push this frame's (azimuth, Doppler) and return the correlation."""
        self.az_buf[:-1] = self.az_buf[1:]
        self.dopp_buf[:-1] = self.dopp_buf[1:]
        self.az_buf[-1] = az_mean
        self.dopp_buf[-1] = weighted_doppler
        if self.frame_count < self.len_corr - 1:
            return 0.0
        a = self.az_buf - self.az_buf.mean()
        d = self.dopp_buf - self.dopp_buf.mean()
        # gesture.c omits the 1/N on both sigma and the cross term, which
        # cancels; eps guards a completely static buffer.
        denom = np.sqrt((a * a).sum()) * np.sqrt((d * d).sum()) + 1e-16
        return float((a * d).sum() / denom)

    def push(self, features: Sequence[float]) -> np.ndarray:
        """Normalise one frame's six features and return the 90-vector."""
        self.frame_count += 1
        self.window[:-1] = self.window[1:]
        self.window[-1] = self.ann.normalise(features)
        return self.window.reshape(-1)

    @property
    def ready(self) -> bool:
        """True once the window holds only real frames.

        The board does not wait -- it infers from frame 1 with a window of
        zeros -- but a zero in normalised space is the mean of the training
        set, not "no data", so early frames produce confident nonsense.  The
        count thresholds in `postproc` absorb it on the board; here you can
        simply not report until the window is full.
        """
        return self.frame_count >= self.feature_len


def order_for_ann(named: dict) -> list:
    """Pick the six ANN inputs out of a dict of named features, in order."""
    return [float(named[k]) for k in ANN_FEATURE_ORDER]


# ---------------------------------------------------------------------------
# Host-side extractors -- stock out-of-box firmware
# ---------------------------------------------------------------------------


@dataclass
class HostFeatureConfig:
    """Geometry and gating for the host extractors.

    `range_min_m`/`range_max_m` replace TI's hard-coded range bins 1..7.  The
    defaults reproduce their window (5 cm to 38 cm); widen them if you are
    retraining for a longer working distance, and remember that
    `weightedRange` then has a different meaning and the model must be
    retrained, not merely rescaled.
    """
    range_min_m: float = 0.05
    range_max_m: float = 0.40
    doppler_min_mps: float = 0.0
    """Speed below which a point is ignored, the analogue of
    DOPPLER_BINS_TO_SUPPRESS.  0 keeps everything; 0.47 reproduces TI."""
    snr_min_db: float = 6.0
    max_points: int = NUM_SORTED_VALUES
    """Keep only the strongest N points, so the feature vector has constant
    statistical weight frame to frame the way TI's fixed-count loop does."""
    range_bin_m: float = 0.0535
    """Used only to express weightedRange in bins, so that the feature has
    the same units as TI's.  Set it from your cfg's range resolution."""


class PointCloudFeatureExtractor:
    """The ten features from TLV 1 + TLV 7, at full frame rate.

    Deliberately mirrors the TI definitions term for term -- magnitude-
    weighted moments over a gated set of cells -- so the same network
    architecture and the same post-processing apply.  What differs:

      weight       SNR in linear units, not detection-matrix magnitude.
      cells        CFAR detections, not the top-N heat-map cells.  CFAR
                   returns a variable number, which is why `max_points`
                   truncates to a fixed count.
      angles       from the point cloud's x/y/z, in DEGREES, not angle-FFT
                   bin indices.  A 32-bin FFT over a 2-element aperture puts
                   roughly 3.6 deg in a bin near boresight, so TI's features
                   are about 3.6x smaller than these for the same geometry.

    None of those are fatal, and all of them mean you must retrain.
    """

    def __init__(self, cfg: Optional[HostFeatureConfig] = None):
        self.cfg = cfg or HostFeatureConfig()

    def __call__(self, frame) -> dict:
        c = self.cfg
        named = {k: 0.0 for k in TLV_FEATURE_ORDER}

        pts = getattr(frame, "points", None)
        if pts is None or len(pts) == 0:
            return named

        r = np.asarray(frame.range_m, dtype=np.float64)
        v = np.asarray(pts["doppler"], dtype=np.float64)
        az = np.asarray(frame.azimuth_deg, dtype=np.float64)
        el = np.asarray(frame.elevation_deg, dtype=np.float64)

        snr_db = frame.snr_db
        if snr_db is None:
            # No side info: weight every point equally rather than silently
            # producing moments that are not comparable with a run that had
            # it.  Callers should enable TLV 7 (guiMonitor detected objects).
            w = np.ones(len(pts), dtype=np.float64)
            snr_db = np.full(len(pts), 99.0, dtype=np.float64)
        else:
            snr_db = np.asarray(snr_db, dtype=np.float64)
            w = 10.0 ** (snr_db / 10.0)

        keep = ((r >= c.range_min_m) & (r <= c.range_max_m)
                & (np.abs(v) >= c.doppler_min_mps) & (snr_db >= c.snr_min_db))
        if not keep.any():
            return named
        r, v, az, el, w = r[keep], v[keep], az[keep], el[keep], w[keep]

        if c.max_points and w.size > c.max_points:
            idx = np.argsort(w)[-c.max_points:]
            r, v, az, el, w = r[idx], v[idx], az[idx], el[idx], w[idx]

        tot = float(w.sum())
        if tot <= 0:
            return named

        named["weightedDoppler"] = float((w * v).sum() / tot)
        pos, neg = v >= 0, v < 0
        if w[pos].sum() > 0:
            named["weightedPositiveDoppler"] = float(
                (w[pos] * v[pos]).sum() / w[pos].sum())
        if w[neg].sum() > 0:
            named["weightedNegativeDoppler"] = float(
                (w[neg] * v[neg]).sum() / w[neg].sum())
        named["weightedRange"] = float((w * r).sum() / tot / c.range_bin_m)
        named["numDetections"] = float(w.size)

        az_m = float((w * az).sum() / tot)
        el_m = float((w * el).sum() / tot)
        named["weightedAzimuthMean"] = az_m
        named["weightedElevationMean"] = el_m
        named["weightedAzimuthDispersion"] = float(
            (w * az * az).sum() / tot - az_m * az_m)
        named["weightedElevationDispersion"] = float(
            (w * el * el).sum() / tot - el_m * el_m)
        # azimuthDopplerCorr is filled in by the pipeline, which owns the
        # 20-frame buffer.
        return named


class HeatmapFeatureExtractor:
    """TI's RDI features, computed on the host from TLV 5.

    Gives weightedDoppler / +ve / -ve / weightedRange / numDetections with
    TI's exact definitions but on the out-of-box detection matrix, which is
    log2 magnitude summed over all virtual antennas rather than linear
    magnitude on one.  `linearise=True` raises 2**x per cell first, which at
    least restores a magnitude-like weighting; it does not undo the antenna
    summation and does not make the TI weights applicable.

    The angle features are absent here by construction: TLV 5 has no phase.
    The pipeline pairs this with the point cloud for those two columns.
    """

    def __init__(self, cfg: Optional[HostFeatureConfig] = None,
                 linearise: bool = True,
                 log2lin_scale: float = 1.0 / 512.0,
                 threshold: Optional[float] = None,
                 n_suppress: int = DOPPLER_BINS_TO_SUPPRESS):
        self.cfg = cfg or HostFeatureConfig()
        self.linearise = linearise
        # cfgparse derives this per device/SDK as
        #   (1/512) * 2**ceil(log2(nva)) / nva      (cfgparse.py:618)
        # and it is what range_profile_linear uses.  Pass
        # `parse_cfg(path).log2lin_scale` rather than trusting the default.
        self.log2lin_scale = log2lin_scale
        self.threshold = threshold
        self.n_suppress = n_suppress

    def __call__(self, frame, range_bin_m: Optional[float] = None) -> dict:
        named = {k: 0.0 for k in TLV_FEATURE_ORDER}
        hm = getattr(frame, "range_doppler_heatmap", None)
        if hm is None or hm.ndim != 2:
            return named

        # tlv.py hands back [doppler][range]; gesture.c wants [range][doppler].
        det = np.asarray(hm, dtype=np.float64).T
        if self.linearise:
            det = np.exp2(det * self.log2lin_scale)

        det = suppress_zero_doppler(det, self.n_suppress, copy=False)

        rb = range_bin_m or self.cfg.range_bin_m
        r0 = max(1, int(round(self.cfg.range_min_m / rb)))
        r1 = min(det.shape[0], int(round(self.cfg.range_max_m / rb)) + 1)
        thr = self.threshold
        if thr is None:
            # Scale-free stand-in for TI's absolute 2000: cells well above the
            # frame's own median.  An absolute threshold is meaningless once
            # the magnitude scale has changed.
            nz = det[r0:r1][det[r0:r1] > 0]
            thr = float(np.median(nz) * 8.0) if nz.size else np.inf

        f = rdi_features(det, r0, r1, thr)
        named["weightedDoppler"] = f.weighted_doppler
        named["weightedPositiveDoppler"] = f.weighted_doppler_pos
        named["weightedNegativeDoppler"] = f.weighted_doppler_neg
        named["weightedRange"] = f.weighted_range
        named["numDetections"] = f.num_detections
        return named
