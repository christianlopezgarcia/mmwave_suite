"""
cfgparse.py -- Parse an mmWave demo .cfg and derive every scaling constant the
TI visualizer derives, using TI's own arithmetic.

Ported line-for-line from:
    C:\\ti\\guicomposer\\runtime\\gcruntime.v11\\mmWave_Demo_Visualizer\\app\\input_validations.js

Cross-references are given as `input_validations.js:<line>`.

Two things this module exists to make explicit:

  1. The Q-format / scaling constants (log2linScale, dspFftScaleComp, toDB,
     rangeIdxToMeters, dopplerResolutionMps) are computed in the *app*, not
     documented in the stream spec.  Without them the uint16 range profile is
     an uninterpretable integer.

  2. `OnChipFilters` surfaces the settings that cause the radar DSP to DROP
     detections before they ever reach the UART.  This is where point loss
     actually happens -- not in the visualizer.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .tlv import Geometry


# ---------------------------------------------------------------------------
# Raw command containers
# ---------------------------------------------------------------------------


@dataclass
class ProfileCfg:
    profile_id: int
    start_freq_ghz: float
    idle_time_us: float
    adc_start_time: float          # input_validations.js:833 stores raw token
    ramp_end_time_us: float
    freq_slope_mhz_per_us: float
    num_adc_samples: int
    dig_out_sample_rate_ksps: float

    # derived (input_validations.js:321-334)
    freq_slope_actual: float = 0.0
    start_freq_actual: float = 0.0
    center_freq_actual: float = 0.0


@dataclass
class OnChipFilters:
    """Settings that make the radar DSP discard detections before UART output.

    Every one of these removes points you will never see, in the .dat or
    anywhere else.  For multipath / ghost work these are the real gates.
    """

    # cfarCfg <subFrame> <procDirection> <mode> <noiseWin> <guardLen>
    #         <divShift> <cyclicMode> <thresholdScale> <peakGrouping>
    cfar_range_threshold_db: Optional[float] = None
    cfar_doppler_threshold_db: Optional[float] = None
    cfar_range_peak_grouping: Optional[int] = None
    cfar_doppler_peak_grouping: Optional[int] = None

    # cfarFovCfg <subFrame> <procDirection> <min> <max>
    range_fov_min_m: Optional[float] = None
    range_fov_max_m: Optional[float] = None
    doppler_fov_min_mps: Optional[float] = None
    doppler_fov_max_mps: Optional[float] = None

    # aoaFovCfg <subFrame> <minAzim> <maxAzim> <minElev> <maxElev>
    azimuth_fov_min_deg: Optional[float] = None
    azimuth_fov_max_deg: Optional[float] = None
    elevation_fov_min_deg: Optional[float] = None
    elevation_fov_max_deg: Optional[float] = None

    clutter_removal: Optional[int] = None
    multi_obj_beam_forming_enabled: Optional[int] = None
    multi_obj_beam_forming_threshold: Optional[float] = None
    extended_max_velocity: Optional[int] = None
    calib_dc_range_sig_enabled: Optional[int] = None

    def report(self) -> str:
        L = []
        A = L.append
        A("On-chip detection gating (applied on the radar DSP, before UART):")
        A("  CFAR range threshold      : %s dB" % _fmt(self.cfar_range_threshold_db))
        A("  CFAR doppler threshold    : %s dB" % _fmt(self.cfar_doppler_threshold_db))
        A("  Range peak grouping       : %s" % _onoff(self.cfar_range_peak_grouping))
        A("  Doppler peak grouping     : %s" % _onoff(self.cfar_doppler_peak_grouping))
        A("  Range FoV gate            : %s .. %s m"
          % (_fmt(self.range_fov_min_m), _fmt(self.range_fov_max_m)))
        A("  Doppler FoV gate          : %s .. %s m/s"
          % (_fmt(self.doppler_fov_min_mps), _fmt(self.doppler_fov_max_mps)))
        A("  Azimuth FoV gate          : %s .. %s deg"
          % (_fmt(self.azimuth_fov_min_deg), _fmt(self.azimuth_fov_max_deg)))
        A("  Elevation FoV gate        : %s .. %s deg"
          % (_fmt(self.elevation_fov_min_deg), _fmt(self.elevation_fov_max_deg)))
        A("  Static clutter removal    : %s" % _onoff(self.clutter_removal))
        A("  Multi-object beamforming  : %s (threshold %s)"
          % (_onoff(self.multi_obj_beam_forming_enabled),
             _fmt(self.multi_obj_beam_forming_threshold)))
        A("  Extended max velocity     : %s" % _onoff(self.extended_max_velocity))
        A("  DC range calibration      : %s" % _onoff(self.calib_dc_range_sig_enabled))
        return "\n".join(L)


@dataclass
class GuiMonitor:
    """guiMonitor <subFrame> <detectedObjects> <logMagRange> <noiseProfile>
                  <rangeAzimuthHeatMap> <rangeDopplerHeatMap> <statsInfo>

    Controls which TLVs the device emits.  Note `detectedObjects`:
       0 = no point cloud at all
       1 = point cloud AND side info (TLV 1 + TLV 7)
       2 = point cloud only (TLV 1, no side info)
    """

    detected_objects: int = 0
    log_mag_range: int = 0
    noise_profile: int = 0
    range_azimuth_heatmap: int = 0
    range_doppler_heatmap: int = 0
    stats_info: int = 0

    def expected_tlvs(self) -> List[int]:
        t: List[int] = []
        if self.detected_objects in (1, 2):
            t.append(1)
        if self.log_mag_range:
            t.append(2)
        if self.noise_profile:
            t.append(3)
        if self.range_azimuth_heatmap:
            t.append(4)
        if self.range_doppler_heatmap:
            t.append(5)
        if self.stats_info:
            t.append(6)
        if self.detected_objects == 1:
            t.append(7)
        if self.stats_info:
            t.append(9)
        return t


@dataclass
class RadarConfig:
    lines: List[str] = field(default_factory=list)
    platform: str = ""
    sdk_version_str: str = ""
    sdk_version_uint16: int = 0

    dfe_mode: int = 1
    num_rx_ant: int = 0
    num_tx_ant: int = 0
    num_tx_azim_ant: int = 0
    num_tx_elev_ant: int = 0
    num_virtual_ant: int = 0

    num_chirps_per_frame: int = 0
    num_doppler_chirps: int = 0
    num_doppler_bins: int = 0
    num_range_bins: int = 0

    tx_chirp_index: Dict[int, int] = field(default_factory=dict)
    """{tx_antenna_index: chirp slot it transmits in}, -1 if disabled.

    The AOP azimuth-elevation heat map (TLV 8) selects which virtual-antenna
    symbols to use from the *transmission order*, not the antenna number
    (mmWave.js:3095-3217), so this mapping is required to reproduce plot 4.
    """

    frame_periodicity_ms: float = 0.0
    num_frames: int = 0

    range_resolution_m: float = 0.0
    range_idx_to_meters: float = 0.0
    range_bias_m: float = 0.0
    """compRangeBiasAndRxChanPhase field 1.  TI subtracts this from the range
    axis of the range profile and both heat maps and clamps at 0
    (mmWave.js:2875, 3310, 2985), but NOT from the point cloud."""
    max_range_m: float = 0.0
    doppler_resolution_mps: float = 0.0
    max_velocity_mps: float = 0.0

    # Range-profile scaling (input_validations.js:352-365, 383-437)
    log2lin_scale: float = 0.0
    to_db: float = 0.0
    dsp_fft_scale_comp_all_lin: float = 1.0
    dsp_fft_scale_comp_all_log: float = 0.0

    profile: Optional[ProfileCfg] = None
    gui_monitor: GuiMonitor = field(default_factory=GuiMonitor)
    filters: OnChipFilters = field(default_factory=OnChipFilters)
    warnings: List[str] = field(default_factory=list)

    # -- outputs ----------------------------------------------------------

    def geometry(self) -> Geometry:
        return Geometry(
            num_range_bins=self.num_range_bins,
            num_doppler_bins=self.num_doppler_bins,
            num_tx_azim_ant=self.num_tx_azim_ant,
            num_rx_ant=self.num_rx_ant,
            num_virtual_ant=self.num_virtual_ant,
        )

    def range_axis_m(self):
        """Range axis for the range profile and heat maps.

        mmWave.js:2874-2878 subtracts compRxChanCfg.rangeBias and clamps at 0.
        Deliberately NOT applied to the point cloud, matching TI.
        """
        import numpy as np
        return np.maximum(
            np.arange(self.num_range_bins) * self.range_idx_to_meters
            - self.range_bias_m, 0.0)

    def doppler_axis_mps(self):
        import numpy as np
        n = self.num_doppler_bins
        return (np.arange(n) - n // 2) * self.doppler_resolution_mps

    def range_profile_db(self, raw):
        """uint16 log-magnitude -> dB.

        input_validations.js:365 + mmWave.js:2871:
            dB = value * log2linScale * (20*log10(2)) + dspFftScaleCompAll_log

        This conversion is entirely app-side; the SDK stream spec documents the
        TLV only as "array of uint16".
        """
        import numpy as np
        return (np.asarray(raw, dtype=np.float64) * self.log2lin_scale * self.to_db
                + self.dsp_fft_scale_comp_all_log)

    def range_profile_linear(self, raw):
        """mmWave.js:2867"""
        import numpy as np
        return self.dsp_fft_scale_comp_all_lin * np.power(
            2.0, np.asarray(raw, dtype=np.float64) * self.log2lin_scale)

    def summary(self) -> str:
        L = []
        A = L.append
        A("Platform            : %s" % (self.platform or "<not stated in cfg>"))
        A("SDK (from cfg)      : %s" % (self.sdk_version_str or "<not stated>"))
        A("Antennas            : %d Tx x %d Rx = %d virtual"
          % (self.num_tx_ant, self.num_rx_ant, self.num_virtual_ant))
        A("Range bins          : %d   (res %.4f m, idx->m %.6f, max %.2f m)"
          % (self.num_range_bins, self.range_resolution_m,
             self.range_idx_to_meters, self.max_range_m))
        A("Doppler bins        : %d   (res %.4f m/s, max +-%.3f m/s)"
          % (self.num_doppler_bins, self.doppler_resolution_mps, self.max_velocity_mps))
        A("Chirps/frame        : %d  (doppler chirps %d)"
          % (self.num_chirps_per_frame, self.num_doppler_chirps))
        A("Frame periodicity   : %.1f ms (%.2f fps)"
          % (self.frame_periodicity_ms,
             1000.0 / self.frame_periodicity_ms if self.frame_periodicity_ms else 0.0))
        A("Range-profile scale : log2lin %.8g, toDB %.6f, fftCompLog %.4f dB"
          % (self.log2lin_scale, self.to_db, self.dsp_fft_scale_comp_all_log))
        A("Expected TLV types  : %s" % (self.gui_monitor.expected_tlvs(),))
        A("")
        A(self.filters.report())
        if self.warnings:
            A("")
            A("Warnings:")
            for w in self.warnings:
                A("  - %s" % w)
        return "\n".join(L)


def _fmt(v) -> str:
    return "?" if v is None else ("%g" % v)


def _onoff(v) -> str:
    if v is None:
        return "?"
    return "ENABLED" if v else "disabled"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _popcount(x: int) -> int:
    return bin(x).count("1")


def _dsp_fft_scal_comp2(fft_min_size: int, fft_size: int) -> float:
    """mmWave.js:1808-1812 -- literally fftMinSize / fftSize.

    Not 1/2^log2(fftSize//fftMinSize): the two agree only when both are exact
    powers of two, and diverge on the numRangeBins==1022 path (MMWSDK-1627),
    where the floor-div form is 0.26 dB out and can divide by zero.
    """
    if fft_size <= 0:
        return 1.0
    return float(fft_min_size) / float(fft_size)


def _dsp_fft_scal_comp1(fft_min_size: int, fft_size: int) -> float:
    """mmWave.js:1814-1820.

        smin = ceil(log2(fftMinSize)/log2(4) - 1)^2 / fftMinSize
        sLin = ceil(log2(fftSize)/log2(4)   - 1)^2 / fftSize
        return sLin / smin
    """
    if fft_size <= 0 or fft_min_size <= 0:
        return 1.0
    smin = (math.ceil(math.log2(fft_min_size) / math.log2(4) - 1)) ** 2 / fft_min_size
    slin = (math.ceil(math.log2(fft_size) / math.log2(4) - 1)) ** 2 / fft_size
    if smin == 0:
        return 1.0
    return slin / smin


def _to_db(lin: float) -> float:
    return 20 * math.log10(lin) if lin > 0 else 0.0


def parse_cfg(path_or_lines) -> RadarConfig:
    """Parse a .cfg file (path or list of lines) into a RadarConfig."""
    if isinstance(path_or_lines, (str, bytes)):
        with open(path_or_lines, "r", errors="replace") as f:
            lines = f.read().splitlines()
    else:
        lines = list(path_or_lines)

    cfg = RadarConfig(lines=lines)

    # ---- header comments emitted by the visualizer -----------------------
    for ln in lines:
        m = re.match(r"^%\s*Platform\s*:\s*(\S+)", ln)
        if m:
            cfg.platform = m.group(1)
        m = re.match(r"^%\s*Created for SDK ver\s*:\s*([\d.]+)", ln)
        if m:
            cfg.sdk_version_str = m.group(1)
            parts = m.group(1).split(".")
            if len(parts) >= 2:
                cfg.sdk_version_uint16 = (int(parts[0]) << 8) | int(parts[1])

    chirp_tx_masks: Dict[int, int] = {}
    frame_chirp_start = frame_chirp_end = num_loops = 0
    rx_channel_en = tx_channel_en = 0

    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("%"):
            continue
        t = s.split()
        cmd = t[0]

        try:
            if cmd == "dfeDataOutputMode":
                cfg.dfe_mode = int(t[1])

            elif cmd == "channelCfg":
                rx_channel_en = int(t[1])
                tx_channel_en = int(t[2])

            elif cmd == "profileCfg":
                cfg.profile = ProfileCfg(
                    profile_id=int(t[1]),
                    start_freq_ghz=float(t[2]),
                    idle_time_us=float(t[3]),
                    adc_start_time=float(t[4]),
                    ramp_end_time_us=float(t[5]),
                    freq_slope_mhz_per_us=float(t[8]),
                    num_adc_samples=int(t[10]),
                    dig_out_sample_rate_ksps=float(t[11]),
                )

            elif cmd == "chirpCfg":
                start_idx, end_idx = int(t[1]), int(t[2])
                tx_mask = int(t[8])
                for i in range(start_idx, end_idx + 1):
                    chirp_tx_masks[i] = tx_mask

            elif cmd == "frameCfg":
                frame_chirp_start = int(t[1])
                frame_chirp_end = int(t[2])
                num_loops = int(t[3])
                cfg.num_frames = int(t[4])
                cfg.frame_periodicity_ms = float(t[5])

            elif cmd == "guiMonitor":
                g = cfg.gui_monitor
                g.detected_objects = int(t[2])
                g.log_mag_range = int(t[3])
                g.noise_profile = int(t[4])
                g.range_azimuth_heatmap = int(t[5])
                g.range_doppler_heatmap = int(t[6])
                g.stats_info = int(t[7])

            elif cmd == "cfarCfg":
                direction = int(t[2])
                thr = float(t[8])
                grouping = int(t[9]) if len(t) > 9 else None
                if direction == 0:
                    cfg.filters.cfar_range_threshold_db = thr
                    cfg.filters.cfar_range_peak_grouping = grouping
                else:
                    cfg.filters.cfar_doppler_threshold_db = thr
                    cfg.filters.cfar_doppler_peak_grouping = grouping

            elif cmd == "cfarFovCfg":
                direction = int(t[2])
                lo, hi = float(t[3]), float(t[4])
                if direction == 0:
                    cfg.filters.range_fov_min_m, cfg.filters.range_fov_max_m = lo, hi
                else:
                    cfg.filters.doppler_fov_min_mps, cfg.filters.doppler_fov_max_mps = lo, hi

            elif cmd == "aoaFovCfg":
                cfg.filters.azimuth_fov_min_deg = float(t[2])
                cfg.filters.azimuth_fov_max_deg = float(t[3])
                cfg.filters.elevation_fov_min_deg = float(t[4])
                cfg.filters.elevation_fov_max_deg = float(t[5])

            elif cmd == "clutterRemoval":
                cfg.filters.clutter_removal = int(t[2])

            elif cmd == "multiObjBeamForming":
                cfg.filters.multi_obj_beam_forming_enabled = int(t[2])
                cfg.filters.multi_obj_beam_forming_threshold = float(t[3])

            elif cmd == "extendedMaxVelocity":
                cfg.filters.extended_max_velocity = int(t[2])

            elif cmd == "calibDcRangeSig":
                cfg.filters.calib_dc_range_sig_enabled = int(t[2])

            elif cmd == "compRangeBiasAndRxChanPhase":
                cfg.range_bias_m = float(t[1])

        except (IndexError, ValueError) as e:
            cfg.warnings.append("could not parse %r (%s)" % (s, e))

    _derive(cfg, rx_channel_en, tx_channel_en, chirp_tx_masks,
            frame_chirp_start, frame_chirp_end, num_loops)
    return cfg


def _derive(cfg: RadarConfig, rx_channel_en: int, tx_channel_en: int,
            chirp_tx_masks: Dict[int, int], chirp_start: int,
            chirp_end: int, num_loops: int) -> None:

    # ---- antennas (input_validations.js:790-880, mmWave.js:1927-2068) -----
    cfg.num_rx_ant = _popcount(rx_channel_en)

    # numTxAzimAnt / numTxElevAnt from channelCfg.  The bit layout is
    # platform-specific (input_validations.js:793-806): on 18xx/64xx/68xx Tx0
    # and Tx2 form the azimuth aperture and Tx1 is the elevation offset; on
    # 16xx it is Tx0+Tx1 with no elevation antenna at all.
    plat = cfg.platform
    if plat == "xWR16xx":
        azim = (tx_channel_en & 1) + ((tx_channel_en >> 1) & 1)
        elev = 0
        non_mimo_mask = 3
    else:
        azim = (tx_channel_en & 1) + ((tx_channel_en >> 2) & 1)
        elev = (tx_channel_en >> 1) & 1
        non_mimo_mask = 5

    # MMWSDK-507 (input_validations.js:844-866, mmWave.js:1934-1944): a chirpCfg
    # whose txEnable is the "both azimuth Tx at once" mask is non-MIMO, and
    # forces numTxAzimAnt to 1 regardless of channelCfg.
    first_chirp_mask = chirp_tx_masks.get(chirp_start, 0)
    if first_chirp_mask == non_mimo_mask:
        azim = 1

    cfg.num_tx_azim_ant = azim
    cfg.num_tx_elev_ant = elev

    if plat.endswith("_AOP"):
        # getAntCfgAOP (mmWave.js:1987-2068): numTxAnt is literally the number
        # of chirps in the frame, NOT the number of distinct Tx antennas.  Each
        # chirp in range must drive exactly one Tx (mask 1, 2 or 4).
        cfg.num_tx_ant = chirp_end - chirp_start + 1
        bad = [i for i in range(chirp_start, chirp_end + 1)
               if chirp_tx_masks.get(i, 0) not in (1, 2, 4)]
        if bad:
            cfg.warnings.append(
                "AOP: chirp(s) %s do not drive exactly one Tx antenna "
                "(txEnable must be 1, 2 or 4); TI rejects this configuration."
                % bad)
    else:
        # input_validations.js:176
        cfg.num_tx_ant = cfg.num_tx_elev_ant + cfg.num_tx_azim_ant

    if cfg.num_tx_ant <= 0:
        cfg.num_tx_ant = _popcount(tx_channel_en) or 1
        cfg.warnings.append(
            "could not derive numTxAnt from channelCfg/chirpCfg; fell back to "
            "popcount(txChannelEn)=%d" % cfg.num_tx_ant)

    # Transmission order: which chirp slot each Tx antenna fires in.
    # mmWave.js:3095-3211 derives the heat-map symbol selection from this.
    slot = 0
    cfg.tx_chirp_index = {0: -1, 1: -1, 2: -1}
    for i in range(chirp_start, chirp_end + 1):
        mask = chirp_tx_masks.get(i, 0)
        for tx in range(3):
            if mask & (1 << tx) and cfg.tx_chirp_index[tx] < 0:
                cfg.tx_chirp_index[tx] = slot
                slot += 1

    # TI sets these to `undefined` on the AOP parts (input_validations.js:807-818)
    # because the antenna-on-package array is a 2D patch layout, not the linear
    # Tx0/Tx2-azimuth + Tx1-elevation arrangement the split assumes.  We keep a
    # fallback because it is the only way to size TLV type 4 -- but AOP parts
    # emit TLV type 8 instead, which is sized from num_virtual_ant and needs no
    # guess.  So this only matters if an AOP board ever sends a type-4 TLV, and
    # only if the azimuth heat map is enabled at all.
    if cfg.platform.endswith("_AOP") and cfg.gui_monitor.range_azimuth_heatmap:
        cfg.warnings.append(
            "AOP platform with rangeAzimuthHeatMap enabled: TI leaves "
            "numTxAzimAnt/numTxElevAnt undefined (input_validations.js:807-818). "
            "The azimuth-elevation heat map (TLV 8) is sized exactly from "
            "num_virtual_ant, but if this board emits a legacy TLV 4 instead, "
            "its %d x %d sizing here is a guess -- check the TLV length."
            % (cfg.num_tx_azim_ant, cfg.num_rx_ant))
    cfg.num_virtual_ant = cfg.num_tx_ant * cfg.num_rx_ant

    # ---- chirp / bin counts (input_validations.js:261-308) ---------------
    if cfg.dfe_mode == 1:
        cfg.num_chirps_per_frame = (chirp_end - chirp_start + 1) * num_loops
    else:
        cfg.warnings.append(
            "advanced frame mode (dfeDataOutputMode 3) is not modelled here; "
            "per-subframe geometry must be derived from subFrameCfg.")
    if cfg.num_tx_ant:
        cfg.num_doppler_chirps = cfg.num_chirps_per_frame // cfg.num_tx_ant

    plat = cfg.platform
    ndc = cfg.num_doppler_chirps
    sdk = cfg.sdk_version_uint16
    # input_validations.js:274-293.  The SDK qualifiers matter: plain xWR68xx
    # uses `sdk == 0x0301` (not >=) in the first branch, so at SDK >= 3.2 it
    # falls through to the 16-bin DSP-DPU floor instead.
    if ndc <= 0:
        cfg.num_doppler_bins = 0
    elif ((plat == "xWR18xx" and sdk >= 0x0301 and ndc <= 4)
          or (plat == "xWR68xx" and sdk == 0x0301 and ndc <= 4)
          or (plat == "xWR64xx" and ndc <= 4)
          or (plat == "xWR68xx_AOP" and ndc <= 4)):
        # Jira MMWSDK-1565
        cfg.num_doppler_bins = 8
    elif ((plat == "xWR68xx" and sdk >= 0x0302 and ndc <= 8)
          or (plat == "xWR16xx" and ndc <= 8)):
        # DSP Doppler/AoA DPU floor
        cfg.num_doppler_bins = 16
    else:
        cfg.num_doppler_bins = 1 << math.ceil(math.log2(ndc))

    if cfg.profile:
        p = cfg.profile
        cfg.num_range_bins = 1 << math.ceil(math.log2(p.num_adc_samples))

        # input_validations.js:296-308, Jira MMWSDK-1627.  Undocumented in the
        # stream spec: with 12 virtual antennas the demo uses 1022, not 1024.
        if (cfg.sdk_version_uint16 >= 0x0301
                and cfg.num_virtual_ant == 12
                and cfg.num_range_bins == 1024
                and plat in ("xWR18xx", "xWR68xx", "xWR64xx",
                             "xWR18xx_AOP", "xWR68xx_AOP")):
            cfg.num_range_bins = 1022

        # ---- BSS frequency quantisation (input_validations.js:310-334) ----
        scale = 3.6 if p.start_freq_ghz >= 76 else 2.7
        slope_const = math.trunc(p.freq_slope_mhz_per_us * (1 << 26) / scale)
        p.freq_slope_actual = slope_const * (scale / (1 << 26))
        start_const = math.trunc(p.start_freq_ghz * (1 << 26) / scale)
        p.start_freq_actual = start_const * scale / (1 << 26)

        # NOTE (TI defect, reproduced deliberately for bit-compatibility):
        # input_validations.js:833 reads the cfg's adcStartTime token straight
        # into `adcStartTimeConst`, then line 334 scales it by 10 ns as if it
        # were in device units.  The CLI actually specifies this field in us,
        # so TI's centre frequency is low by ~100x this term.  The term is tiny
        # (sub-ppm of the carrier), so it does not matter numerically -- but it
        # is a real divergence between the app and the SDK CLI.
        p.center_freq_actual = (
            p.start_freq_actual
            + 0.5 * ((p.freq_slope_actual * p.num_adc_samples) / p.dig_out_sample_rate_ksps)
            + (p.freq_slope_actual * (p.adc_start_time * 10 * 1e-9))
        )

        # ---- resolutions (input_validations.js:336-350) -------------------
        cfg.range_resolution_m = (300 * p.dig_out_sample_rate_ksps
                                  / (2 * p.freq_slope_actual * 1e3 * p.num_adc_samples))
        cfg.range_idx_to_meters = (3e8 * p.dig_out_sample_rate_ksps * 1e3
                                   / (2 * abs(p.freq_slope_actual) * 1e12 * cfg.num_range_bins))
        cfg.max_range_m = (300 * 0.8 * p.dig_out_sample_rate_ksps
                           / (2 * p.freq_slope_actual * 1e3))

        chirp_period_s = (p.idle_time_us + p.ramp_end_time_us) * 1e-6
        if cfg.num_tx_ant:
            cfg.max_velocity_mps = 3e8 / (4 * p.center_freq_actual * 1e9
                                          * chirp_period_s * cfg.num_tx_ant)
        if cfg.num_doppler_bins and cfg.num_tx_ant:
            cfg.doppler_resolution_mps = (
                3e8 / (2 * p.center_freq_actual * 1e9 * chirp_period_s
                       * cfg.num_doppler_bins * cfg.num_tx_ant))

        # ---- range-profile scaling (input_validations.js:352-437) ---------
        nva = cfg.num_virtual_ant or 1
        if plat in ("xWR18xx", "xWR68xx") and cfg.sdk_version_uint16 < 0x0302:
            cfg.log2lin_scale = (1 / 512) * (2 ** math.ceil(math.log2(nva))) / nva
        else:
            cfg.log2lin_scale = (1 / 256) * (2 ** math.ceil(math.log2(nva))) / nva
        cfg.to_db = 20 * math.log10(2)

        # input_validations.js:380-437 -- full five-way platform switch.
        # The 1D term follows whether the Range DPU is the DSP or HWA version;
        # the 2D term is 1 except on xWR16xx, and on xWR68xx from SDK 3.2 where
        # the Doppler DPU became the DSP version.
        if plat == "xWR16xx":
            c1 = _dsp_fft_scal_comp1(64, cfg.num_range_bins)     # Range DSP DPU
            c2 = _dsp_fft_scal_comp2(16, cfg.num_doppler_bins)   # Doppler DSP DPU
        elif plat == "xWR68xx":
            c1 = _dsp_fft_scal_comp2(32, cfg.num_range_bins)     # Range HWA DPU
            c2 = (1.0 if sdk < 0x0302
                  else _dsp_fft_scal_comp2(16, cfg.num_doppler_bins))
        else:
            # xWR18xx, xWR18xx_AOP, xWR64xx, xWR68xx_AOP -- and the safe default
            # for an unrecognised platform.
            c1 = _dsp_fft_scal_comp2(32, cfg.num_range_bins)     # Range HWA DPU
            c2 = 1.0                                             # Doppler HWA DPU
            if plat not in ("xWR18xx", "xWR18xx_AOP", "xWR64xx", "xWR68xx_AOP"):
                cfg.warnings.append(
                    "unrecognised platform %r; using the HWA-DPU range-profile "
                    "scaling (correct for 18xx/64xx/68xx_AOP)." % plat)
        cfg.dsp_fft_scale_comp_all_lin = c1 * c2
        cfg.dsp_fft_scale_comp_all_log = _to_db(c1) + _to_db(c2)
