"""
micro_doppler/gait.py -- spatiotemporal gait metrics, paper Eqs. (3)-(6).

    N_steps  = sum_j |{t_k in W_j}|                                   (3)
    cadence  = 60 * N_steps / T_walk            [steps/min]           (4)
    CV_dt    = 100 * sigma(dt_k) / mu(dt_k)     [%]                   (5)
    MAPE     = 100 * |N_steps - N_GT| / N_GT                          (6)

Metrics are computed only from step times inside walking segments, so `T_walk`
is the summed segment duration, not the recording length. Using the recording
length instead silently depresses cadence in any trial containing turns or
pauses -- which is every corridor trial in the reference study.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional

import numpy as np

from .steps import StepEvents


@dataclass
class GaitMetrics:
    n_steps: int
    walking_time_s: float
    cadence_steps_per_min: float
    mean_step_time_s: float
    std_step_time_s: float
    cv_step_time_pct: float
    median_step_time_s: float
    n_segments: int
    recording_duration_s: float
    walking_fraction: float
    mape_pct: Optional[float] = None
    n_steps_ground_truth: Optional[int] = None

    def to_dict(self) -> Dict:
        return asdict(self)

    def report(self) -> str:
        L = [
            "Steps detected        : %d" % self.n_steps,
            "Walking time (T_walk) : %.2f s over %d segment(s) "
            "(%.0f%% of the %.1f s recording)"
            % (self.walking_time_s, self.n_segments,
               100 * self.walking_fraction, self.recording_duration_s),
            "Cadence               : %.1f steps/min" % self.cadence_steps_per_min,
            "Step time             : %.3f s mean, %.3f s median, %.3f s sd"
            % (self.mean_step_time_s, self.median_step_time_s,
               self.std_step_time_s),
            "Step-time variability : %.1f %% (CV_dt)" % self.cv_step_time_pct,
        ]
        if self.mape_pct is not None:
            L.append("MAPE vs ground truth  : %.1f %% (detected %d, truth %d)"
                     % (self.mape_pct, self.n_steps, self.n_steps_ground_truth))
        return "\n".join(L)


def compute_gait_metrics(steps: StepEvents,
                         recording_duration_s: float,
                         n_steps_ground_truth: Optional[int] = None
                         ) -> GaitMetrics:
    """Eqs. (3)-(6)."""
    n = steps.n_steps                                   # Eq. (3)
    t_walk = steps.walking_time
    cadence = 60.0 * n / t_walk if t_walk > 0 else 0.0   # Eq. (4)

    dt = steps.intervals()
    if dt.size:
        mu, sd, med = float(dt.mean()), float(dt.std(ddof=1) if dt.size > 1
                                              else 0.0), float(np.median(dt))
        cv = 100.0 * sd / mu if mu > 0 else 0.0          # Eq. (5)
    else:
        mu = sd = med = cv = 0.0

    mape = None
    if n_steps_ground_truth:
        mape = 100.0 * abs(n - n_steps_ground_truth) / n_steps_ground_truth

    return GaitMetrics(
        n_steps=n,
        walking_time_s=t_walk,
        cadence_steps_per_min=cadence,
        mean_step_time_s=mu,
        std_step_time_s=sd,
        cv_step_time_pct=cv,
        median_step_time_s=med,
        n_segments=len(steps.segments),
        recording_duration_s=recording_duration_s,
        walking_fraction=(t_walk / recording_duration_s
                          if recording_duration_s > 0 else 0.0),
        mape_pct=mape,
        n_steps_ground_truth=n_steps_ground_truth,
    )


def cadence_from_spectrum(env, fps: float,
                          band_hz: tuple = (0.5, 4.0)) -> Dict[str, float]:
    """Independent cadence estimate from the envelope's spectrum.

    Peak-picking and spectral estimation fail differently: peak-picking
    miscounts when peaks merge, the spectrum smears when cadence drifts. If the
    two disagree badly, the step detector's thresholds are probably wrong --
    which is worth knowing before a number goes in a thesis.

    Returns the dominant step frequency in Hz and the implied cadence.
    """
    v = np.where(np.isfinite(env.v), env.v, 0.0)
    v = v - v.mean()
    if v.size < 8 or fps <= 0:
        return dict(step_freq_hz=float("nan"),
                    cadence_steps_per_min=float("nan"), confidence=0.0)

    w = np.hanning(v.size)
    spec = np.abs(np.fft.rfft(v * w))
    freq = np.fft.rfftfreq(v.size, d=1.0 / fps)

    band = (freq >= band_hz[0]) & (freq <= band_hz[1])
    if not band.any():
        return dict(step_freq_hz=float("nan"),
                    cadence_steps_per_min=float("nan"), confidence=0.0)

    sb = np.where(band, spec, 0.0)
    k = int(np.argmax(sb))
    total = sb.sum()
    return dict(
        step_freq_hz=float(freq[k]),
        cadence_steps_per_min=float(freq[k] * 60.0),
        confidence=float(sb[k] / total) if total > 0 else 0.0,
    )
