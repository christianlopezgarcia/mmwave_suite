"""
core/recording.py -- the in-memory representation of one capture.

A `Recording` is the single object every analysis function consumes. It holds
the decoded frames plus the acquisition geometry, so no downstream module ever
has to know about TLVs, .cfg syntax, or Q-formats.

Fidelity tiers, because this matters for interpreting every result:

  TIER A  raw complex ADC  -- what the reference paper uses. Requires LVDS /
          DCA1000 capture. NOT available over the TI demo's UART.
  TIER B  range-Doppler map (TLV 5) -- RD(r,v,t) directly, exactly the quantity
          the paper marginalises in Eq. (1). Available over UART but bandwidth
          limited; see docs/FIDELITY.md.
  TIER C  range profile (TLV 2) + detected point cloud (TLV 1/7) -- what a
          default mmw-demo config emits. Dense in range, sparse in Doppler.

`Recording.tier` reports which of these the loaded file actually supports, and
the map builders refuse to fabricate what is not there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np


@dataclass
class Geometry:
    """Acquisition geometry, derived from the .cfg by TI's own arithmetic."""

    num_range_bins: int = 0
    num_doppler_bins: int = 0
    num_doppler_chirps: int = 0
    range_idx_to_meters: float = 0.0
    range_resolution_m: float = 0.0
    doppler_resolution_mps: float = 0.0
    max_velocity_mps: float = 0.0
    frame_period_s: float = 0.0
    num_tx_ant: int = 0
    num_rx_ant: int = 0
    range_bias_m: float = 0.0
    platform: str = ""

    @property
    def fps(self) -> float:
        return 1.0 / self.frame_period_s if self.frame_period_s else 0.0

    def range_axis(self) -> np.ndarray:
        """Range of each bin, in metres. Bias-corrected and clamped at 0."""
        return np.maximum(
            np.arange(self.num_range_bins) * self.range_idx_to_meters
            - self.range_bias_m, 0.0)

    def velocity_axis(self) -> np.ndarray:
        """Velocity of each Doppler bin, in m/s, fftshifted (negative first)."""
        n = self.num_doppler_bins
        return (np.arange(n) - n // 2) * self.doppler_resolution_mps

    @property
    def true_velocity_resolution_mps(self) -> float:
        """CPI-limited resolution -- what actually governs separating two
        scatterers. Differs from bin spacing when the FFT is zero-padded."""
        if not self.num_doppler_chirps:
            return self.doppler_resolution_mps
        return (self.doppler_resolution_mps
                * self.num_doppler_bins / self.num_doppler_chirps)


@dataclass
class OnChipGating:
    """What the radar DSP discarded before anything reached the host.

    Carried through the whole pipeline because no host-side analysis can undo
    it, and every conclusion about "how many points" is conditional on it.
    """

    cfar_range_threshold_db: Optional[float] = None
    cfar_doppler_threshold_db: Optional[float] = None
    range_fov_m: Optional[tuple] = None
    doppler_fov_mps: Optional[tuple] = None
    azimuth_fov_deg: Optional[tuple] = None
    elevation_fov_deg: Optional[tuple] = None
    clutter_removal: Optional[int] = None
    multi_obj_beamforming: Optional[int] = None
    multi_obj_threshold: Optional[float] = None
    extended_max_velocity: Optional[int] = None

    def summary(self) -> str:
        def rng(t, u):
            return "full" if t is None else "%g .. %g %s" % (t[0], t[1], u)
        return "\n".join([
            "  CFAR thresholds      : range %s dB, doppler %s dB"
            % (self.cfar_range_threshold_db, self.cfar_doppler_threshold_db),
            "  Range gate           : %s" % rng(self.range_fov_m, "m"),
            "  Doppler gate         : %s" % rng(self.doppler_fov_mps, "m/s"),
            "  Static clutter removal: %s"
            % ("ON" if self.clutter_removal else "off"),
            "  Multi-obj beamforming : %s (thr %s)  <- ghost generator"
            % ("ON" if self.multi_obj_beamforming else "off",
               self.multi_obj_threshold),
        ])


@dataclass
class Recording:
    """One capture, fully decoded."""

    name: str
    path: str
    geom: Geometry
    gating: OnChipGating = field(default_factory=OnChipGating)

    # --- per-frame time base -------------------------------------------
    frame_numbers: np.ndarray = field(default_factory=lambda: np.zeros(0, int))
    t: np.ndarray = field(default_factory=lambda: np.zeros(0, float))
    """Seconds from the first frame. Derived from frame_number * frame_period
    (deterministic), not from host arrival time."""

    # --- TIER C: point cloud, one variable-length array per frame -------
    points: List[np.ndarray] = field(default_factory=list)
    """Per frame, a structured array with fields x, y, z, doppler (float32)."""
    side_info: List[Optional[np.ndarray]] = field(default_factory=list)
    """Per frame, structured array with fields snr, noise (int16, 0.1 dB)."""

    # --- TIER C: dense range profile (TLV 2) ---------------------------
    range_profile: Optional[np.ndarray] = None
    """(num_frames, num_range_bins) float, in dB. None if TLV 2 absent."""
    noise_profile: Optional[np.ndarray] = None

    # --- TIER B: range-Doppler map (TLV 5) -----------------------------
    rd_map: Optional[np.ndarray] = None
    """(num_frames, num_doppler_bins, num_range_bins) float. None if absent."""

    cfg_path: str = ""
    cfg_is_fallback: bool = False
    """True when no .cfg was found beside the .dat and a library default was
    substituted. Every axis is then provisional -- carried into the manifest
    so a figure can never quietly claim more certainty than it has."""

    stats: List[Optional[Dict]] = field(default_factory=list)
    anomalies: Dict[str, int] = field(default_factory=dict)
    lost_bytes: int = 0
    resyncs: int = 0

    # -- properties -------------------------------------------------------

    @property
    def num_frames(self) -> int:
        return len(self.frame_numbers)

    @property
    def duration_s(self) -> float:
        return float(self.t[-1] - self.t[0]) if self.num_frames > 1 else 0.0

    @property
    def tier(self) -> str:
        if self.rd_map is not None:
            return "B (range-Doppler map)"
        if self.range_profile is not None:
            return "C+ (range profile + point cloud)"
        return "C (point cloud only)"

    @property
    def total_points(self) -> int:
        return int(sum(p.size for p in self.points if p is not None))

    # -- flattened views, the form most analyses want ----------------------

    def flat(self) -> Dict[str, np.ndarray]:
        """Every point from every frame, flattened, with a time and frame index.

        Returns arrays of identical length: t, frame_idx, x, y, z, v, range,
        azimuth_deg, elevation_deg, snr_db, noise_db.
        """
        ts, fi, xs, ys, zs, vs, sn, no = [], [], [], [], [], [], [], []
        for i, p in enumerate(self.points):
            if p is None or p.size == 0:
                continue
            n = p.size
            ts.append(np.full(n, self.t[i]))
            fi.append(np.full(n, i, dtype=np.int32))
            xs.append(p["x"].astype(np.float64))
            ys.append(p["y"].astype(np.float64))
            zs.append(p["z"].astype(np.float64))
            vs.append(p["doppler"].astype(np.float64))
            si = self.side_info[i] if i < len(self.side_info) else None
            if si is not None and si.size == n:
                sn.append(si["snr"].astype(np.float64) * 0.1)
                no.append(si["noise"].astype(np.float64) * 0.1)
            else:
                sn.append(np.full(n, np.nan))
                no.append(np.full(n, np.nan))

        if not ts:
            z = np.zeros(0)
            return dict(t=z, frame_idx=np.zeros(0, np.int32), x=z, y=z, z=z,
                        v=z, range=z, azimuth_deg=z, elevation_deg=z,
                        snr_db=z, noise_db=z)

        x = np.concatenate(xs); y = np.concatenate(ys); zz = np.concatenate(zs)
        rng = np.sqrt(x * x + y * y + zz * zz)
        return dict(
            t=np.concatenate(ts),
            frame_idx=np.concatenate(fi),
            x=x, y=y, z=zz,
            v=np.concatenate(vs),
            range=rng,
            # atan2, not atan(x/y): atan folds the y<0 half-plane onto y>0, so a
            # target behind the array reports at the mirrored forward angle --
            # fatal when the thing you are studying IS mirror images.
            azimuth_deg=np.degrees(np.arctan2(x, y)),
            elevation_deg=np.degrees(np.arctan2(zz, np.hypot(x, y))),
            snr_db=np.concatenate(sn),
            noise_db=np.concatenate(no),
        )

    def summary(self) -> str:
        g = self.geom
        return "\n".join([
            "Recording      : %s" % self.name,
            "Fidelity tier  : %s" % self.tier,
            "Frames         : %d over %.1f s (%.1f fps)"
            % (self.num_frames, self.duration_s, g.fps),
            "Range          : %d bins, %.4f m resolution, %.2f m span"
            % (g.num_range_bins, g.range_resolution_m,
               g.num_range_bins * g.range_idx_to_meters),
            "Velocity       : %d bins, %.4f m/s spacing (%.4f m/s true), "
            "max +-%.2f m/s"
            % (g.num_doppler_bins, g.doppler_resolution_mps,
               g.true_velocity_resolution_mps, g.max_velocity_mps),
            "Points         : %d total, %.2f per frame"
            % (self.total_points,
               self.total_points / max(self.num_frames, 1)),
            "Integrity      : %d lost bytes, %d resyncs, anomalies=%s"
            % (self.lost_bytes, self.resyncs, self.anomalies or "none"),
            "Config         : %s%s"
            % (os.path.basename(self.cfg_path) if self.cfg_path else "?",
               "   *** FALLBACK - axes provisional ***"
               if self.cfg_is_fallback else ""),
            "On-chip gating (irreversible, applied before the host saw anything):",
            self.gating.summary(),
        ])
