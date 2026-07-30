"""
micro_doppler/steps.py -- walking-segment selection and step-event extraction.

Paper §2.4: walking intervals {W_j} are found by hysteresis on v_foot(t) with a
minimum duration; step events are peaks in v_foot(t) subject to v_min, a minimum
spacing dt_min, and an adaptive prominence. Metrics are computed only from step
times inside the union of walking segments -- "Segment-Constrained Evaluation".

That constraint is doing real work: turns and pauses produce velocity excursions
that look like steps to a naive peak-picker, and the paper's own results show
step-count error rising sharply exactly where segmentation is hardest
(festination, freezing-of-gait).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np

from .envelope import Envelope

# Paper §2.4: "A minimum peak threshold (v_min = 0.20 m/s) suppresses
# low-velocity fluctuations".
DEFAULT_V_MIN = 0.20


@dataclass
class WalkingSegment:
    start_idx: int
    end_idx: int
    t_start: float
    t_end: float

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start


@dataclass
class StepEvents:
    times: np.ndarray
    indices: np.ndarray
    heights: np.ndarray
    segments: List[WalkingSegment] = field(default_factory=list)
    provenance: str = ""

    @property
    def n_steps(self) -> int:
        return int(self.times.size)

    @property
    def walking_time(self) -> float:
        """T_walk = sum |W_j|."""
        return float(sum(s.duration for s in self.segments))

    def intervals(self) -> np.ndarray:
        """dt_k = t_{k+1} - t_k, only within a single segment."""
        if self.times.size < 2:
            return np.zeros(0)
        d, tt = [], self.times
        for s in self.segments:
            m = (tt >= s.t_start) & (tt <= s.t_end)
            inside = tt[m]
            if inside.size >= 2:
                d.append(np.diff(inside))
        return np.concatenate(d) if d else np.zeros(0)


def find_walking_segments(env: Envelope,
                          v_on: float = 0.35,
                          v_off: float = 0.20,
                          min_duration_s: float = 1.0) -> List[WalkingSegment]:
    """Hysteresis on v_foot(t).

    Two thresholds rather than one: a single threshold chatters on and off
    during the velocity minimum at each stance phase, fragmenting one walk into
    many short segments and destroying the step-time statistics.
    """
    v = env.v
    t = env.t
    if v.size == 0:
        return []

    walking = False
    segs: List[WalkingSegment] = []
    start = 0
    for i, val in enumerate(v):
        if not np.isfinite(val):
            val = 0.0
        if not walking and val >= v_on:
            walking, start = True, i
        elif walking and val < v_off:
            walking = False
            segs.append(WalkingSegment(start, i - 1, float(t[start]),
                                       float(t[i - 1])))
    if walking:
        segs.append(WalkingSegment(start, v.size - 1, float(t[start]),
                                   float(t[-1])))

    return [s for s in segs if s.duration >= min_duration_s]


def detect_steps(env: Envelope,
                 v_min: float = DEFAULT_V_MIN,
                 min_interval_s: float = 0.25,
                 prominence_frac: float = 0.15,
                 segments: List[WalkingSegment] = None,
                 v_on: float = 0.35,
                 v_off: float = 0.20,
                 min_segment_s: float = 1.0) -> StepEvents:
    """Peaks in v_foot(t) inside walking segments.

    `prominence_frac` is adaptive: the absolute prominence threshold is that
    fraction of the envelope's own interquartile spread, so it scales with the
    subject and the geometry instead of being a fixed m/s number that only suits
    one recording.

    min_interval_s = 0.25 admits cadences up to 240 steps/min, comfortably above
    festination, while rejecting double-detections on a single swing.
    """
    if segments is None:
        segments = find_walking_segments(env, v_on, v_off, min_segment_s)

    v = np.where(np.isfinite(env.v), env.v, 0.0)
    t = env.t
    dt = float(np.median(np.diff(t))) if t.size > 1 else 1.0
    min_dist = max(1, int(round(min_interval_s / dt)))

    finite = env.v[np.isfinite(env.v)]
    if finite.size:
        spread = np.percentile(finite, 75) - np.percentile(finite, 25)
    else:
        spread = 0.0
    prominence = max(prominence_frac * spread, 1e-6)

    idx = _find_peaks(v, height=v_min, distance=min_dist, prominence=prominence)

    if segments:
        inside = np.zeros(v.size, bool)
        for s in segments:
            inside[s.start_idx:s.end_idx + 1] = True
        idx = idx[inside[idx]]

    return StepEvents(
        times=t[idx], indices=idx, heights=v[idx], segments=segments,
        provenance=("peaks with v_min=%.2f m/s, min spacing %.2f s, adaptive "
                    "prominence %.4f (%.0f%% of envelope IQR), constrained to "
                    "%d walking segment(s) | %s"
                    % (v_min, min_interval_s, prominence, 100 * prominence_frac,
                       len(segments), env.provenance)))


def _find_peaks(x: np.ndarray, height: float, distance: int,
                prominence: float) -> np.ndarray:
    """Peak picking with height, spacing and prominence constraints.

    Uses scipy when available; otherwise a local implementation so the toolkit
    has no hard scipy dependency for its core path.
    """
    try:
        from scipy.signal import find_peaks
        idx, _ = find_peaks(x, height=height, distance=distance,
                            prominence=prominence)
        return idx
    except ImportError:
        pass

    cand = [i for i in range(1, x.size - 1)
            if x[i] >= x[i - 1] and x[i] > x[i + 1] and x[i] >= height]
    keep: List[int] = []
    for i in sorted(cand, key=lambda j: -x[j]):
        if all(abs(i - j) >= distance for j in keep):
            lo = max(0, i - distance)
            hi = min(x.size, i + distance + 1)
            if x[i] - min(x[lo:hi].min(), x[i]) >= prominence:
                keep.append(i)
    return np.array(sorted(keep), dtype=int)
