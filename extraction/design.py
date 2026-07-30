"""
design.py -- Chirp designer for the mmWave demo.

Computes every derived quantity with TI's exact arithmetic (the same port used
in cfgparse.py), checks the constraints that actually bite, and emits a .cfg.

The Doppler trade-off, which is the whole reason this exists:

    v_max = lambda / (4 * Tc * nTx)                 Tc = idleTime + rampEndTime
    v_res = lambda / (2 * N * Tc * nTx) = 2*v_max/N  N  = numDopplerBins

So v_max and v_res pull in opposite directions unless you raise N. And N is
bounded by the radar cube, which must fit in the device's L3 SRAM:

    cube_bytes = numRangeBins * numDopplerChirps * numVirtualAnt * 4

That single inequality is why you cannot simply ask for "fast and fine": buying
Doppler bins costs range bins or virtual antennas.

Usage:
    python -m mmwave_suite.extraction.design --preset microdoppler --out my.cfg
    python -m mmwave_suite.extraction.design --compare
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from typing import List, Optional

# IWR6843 L3 SRAM available to the radar cube.  The mmw demo will refuse a
# config whose cube does not fit, so an over-budget design fails loudly at
# sensorStart rather than silently.  Treated as a soft budget here.
L3_BUDGET_BYTES = 768 * 1024

# Practical floor for idleTime on the 60 GHz parts.  TI's own Visualizer slider
# bottoms out around here (its 6.39 m/s maximum implies Tc ~= 63 us with this
# profile), which is the best evidence available for what the RF front end will
# actually accept.
MIN_IDLE_TIME_US = 6.0

# Guard between the end of the ADC window and the end of the ramp.
RAMP_GUARD_US = 1.0


@dataclass
class ChirpDesign:
    name: str = "custom"
    description: str = ""

    # profileCfg
    start_freq_ghz: float = 60.0
    idle_time_us: float = 359.0
    adc_start_time_us: float = 7.0
    ramp_end_time_us: float = 57.14
    freq_slope_mhz_per_us: float = 70.0
    tx_start_time_us: float = 1.0
    num_adc_samples: int = 256
    dig_out_sample_rate_ksps: int = 5209
    rx_gain_db: int = 158

    # channel / chirps / frame
    rx_channel_en: int = 15          # 4 Rx
    tx_enable_masks: List[int] = field(default_factory=lambda: [1, 2, 4])
    num_loops: int = 16
    frame_periodicity_ms: float = 100.0

    # detection
    cfar_range_thr_db: float = 15.0
    cfar_doppler_thr_db: float = 15.0
    range_fov_max_m: Optional[float] = None   # None -> full computed range
    doppler_fov_m_s: Optional[float] = None   # None -> full unambiguous range
    clutter_removal: int = 0
    multi_obj_beamforming: int = 1
    extended_max_velocity: int = 0

    # gui
    gui_detected_objects: int = 1
    gui_log_mag_range: int = 1
    gui_noise_profile: int = 0
    gui_range_azimuth_heatmap: int = 0
    gui_range_doppler_heatmap: int = 0
    gui_stats: int = 1

    # ---- derived (TI arithmetic) ----------------------------------------

    @property
    def num_rx_ant(self) -> int:
        return bin(self.rx_channel_en).count("1")

    @property
    def num_tx_ant(self) -> int:
        # AOP: numTxAnt is the chirp count of the frame (mmWave.js:2058)
        return len(self.tx_enable_masks)

    @property
    def num_virtual_ant(self) -> int:
        return self.num_tx_ant * self.num_rx_ant

    @property
    def scale(self) -> float:
        return 3.6 if self.start_freq_ghz >= 76 else 2.7

    @property
    def freq_slope_actual(self) -> float:
        c = math.trunc(self.freq_slope_mhz_per_us * (1 << 26) / self.scale)
        return c * (self.scale / (1 << 26))

    @property
    def start_freq_actual(self) -> float:
        c = math.trunc(self.start_freq_ghz * (1 << 26) / self.scale)
        return c * self.scale / (1 << 26)

    @property
    def center_freq_actual(self) -> float:
        # input_validations.js:332-334.  The adcStartTime term reproduces TI's
        # 10 ns scaling of the raw cfg token; it is sub-ppm either way.
        return (self.start_freq_actual
                + 0.5 * (self.freq_slope_actual * self.num_adc_samples
                         / self.dig_out_sample_rate_ksps)
                + self.freq_slope_actual * (self.adc_start_time_us * 10 * 1e-9))

    @property
    def adc_time_us(self) -> float:
        return 1000.0 * self.num_adc_samples / self.dig_out_sample_rate_ksps

    @property
    def chirp_period_us(self) -> float:
        return self.idle_time_us + self.ramp_end_time_us

    @property
    def num_range_bins(self) -> int:
        n = 1 << math.ceil(math.log2(self.num_adc_samples))
        if self.num_virtual_ant == 12 and n == 1024:
            n = 1022                        # MMWSDK-1627
        return n

    @property
    def num_chirps_per_frame(self) -> int:
        return len(self.tx_enable_masks) * self.num_loops

    @property
    def num_doppler_chirps(self) -> int:
        return self.num_chirps_per_frame // self.num_tx_ant

    @property
    def num_doppler_bins(self) -> int:
        ndc = self.num_doppler_chirps
        if ndc <= 4:
            return 8                        # MMWSDK-1565, AOP
        return 1 << math.ceil(math.log2(ndc))

    @property
    def range_resolution_m(self) -> float:
        return (300 * self.dig_out_sample_rate_ksps
                / (2 * self.freq_slope_actual * 1e3 * self.num_adc_samples))

    @property
    def range_idx_to_meters(self) -> float:
        return (3e8 * self.dig_out_sample_rate_ksps * 1e3
                / (2 * abs(self.freq_slope_actual) * 1e12 * self.num_range_bins))

    @property
    def max_range_m(self) -> float:
        return (300 * 0.8 * self.dig_out_sample_rate_ksps
                / (2 * self.freq_slope_actual * 1e3))

    @property
    def max_velocity_mps(self) -> float:
        return (3e8 / (4 * self.center_freq_actual * 1e9
                       * self.chirp_period_us * 1e-6 * self.num_tx_ant))

    @property
    def doppler_resolution_mps(self) -> float:
        """Doppler BIN SPACING, as TI reports it (input_validations.js:348).

        Note this is derived from numDopplerBins, which is numDopplerChirps
        rounded UP to a power of two.  When those differ, the FFT is zero-padded
        and the extra bins interpolate the peak -- they do not let you separate
        two scatterers any better.  See doppler_true_resolution_mps.
        """
        return (3e8 / (2 * self.center_freq_actual * 1e9
                       * self.chirp_period_us * 1e-6
                       * self.num_doppler_bins * self.num_tx_ant))

    @property
    def doppler_true_resolution_mps(self) -> float:
        """Velocity resolution actually achievable -- set by the CPI.

        Two scatterers closer together than this in radial velocity merge into
        one Doppler peak no matter how many bins the FFT has.  This is the
        number that governs whether you can separate a swinging hand from the
        torso in the same frame.
        """
        return (3e8 / (2 * self.center_freq_actual * 1e9
                       * self.chirp_period_us * 1e-6
                       * self.num_doppler_chirps * self.num_tx_ant))

    @property
    def is_zero_padded(self) -> bool:
        return self.num_doppler_chirps != self.num_doppler_bins

    @property
    def active_frame_time_ms(self) -> float:
        return self.num_chirps_per_frame * self.chirp_period_us / 1000.0

    @property
    def duty_cycle(self) -> float:
        return self.active_frame_time_ms / self.frame_periodicity_ms

    @property
    def radar_cube_bytes(self) -> int:
        # complex int16 per (range bin, doppler chirp, virtual antenna)
        return (self.num_range_bins * self.num_doppler_chirps
                * self.num_virtual_ant * 4)

    # ---- constraints -----------------------------------------------------

    def check(self) -> List[str]:
        """Returns a list of problems.  Empty means it should be accepted."""
        p: List[str] = []
        if self.ramp_end_time_us < self.adc_start_time_us + self.adc_time_us:
            p.append("rampEndTime %.2f us is shorter than adcStartTime + ADC "
                     "window (%.2f us): the ramp ends before sampling finishes"
                     % (self.ramp_end_time_us,
                        self.adc_start_time_us + self.adc_time_us))
        if self.idle_time_us < MIN_IDLE_TIME_US:
            p.append("idleTime %.2f us is below the practical floor of %.1f us"
                     % (self.idle_time_us, MIN_IDLE_TIME_US))
        if self.radar_cube_bytes > L3_BUDGET_BYTES:
            p.append("radar cube %.0f KB exceeds the %.0f KB L3 budget "
                     "(reduce numAdcSamples, numLoops, or Tx count)"
                     % (self.radar_cube_bytes / 1024, L3_BUDGET_BYTES / 1024))
        if self.duty_cycle > 0.9:
            p.append("active chirp time %.1f ms is %.0f%% of the %.0f ms frame; "
                     "leave room for interframe processing"
                     % (self.active_frame_time_ms, 100 * self.duty_cycle,
                        self.frame_periodicity_ms))
        if self.num_loops > 255:
            p.append("numLoops %d exceeds the CLI maximum of 255" % self.num_loops)
        if self.num_adc_samples % 4:
            p.append("numAdcSamples should be a multiple of 4")
        return p

    def warnings(self) -> List[str]:
        w: List[str] = []
        if self.duty_cycle > 0.6:
            w.append("duty cycle %.0f%% is high; interframe processing margin "
                     "will be tight" % (100 * self.duty_cycle))
        if self.radar_cube_bytes > 0.75 * L3_BUDGET_BYTES:
            w.append("radar cube %.0f KB uses %.0f%% of the assumed L3 budget; "
                     "if sensorStart reports a memory error, halve numLoops"
                     % (self.radar_cube_bytes / 1024,
                        100 * self.radar_cube_bytes / L3_BUDGET_BYTES))
        if self.num_tx_ant < 3:
            w.append("fewer than 3 Tx: no elevation aperture, so z is not "
                     "resolved -- this matters for separating ghosts from real "
                     "targets by height")
        return w

    # ---- reporting -------------------------------------------------------

    def summary(self) -> str:
        L = []
        A = L.append
        A("%-22s %s" % ("Design", self.name))
        if self.description:
            A("%-22s %s" % ("", self.description))
        A("%-22s %d Tx x %d Rx = %d virtual"
          % ("Antennas", self.num_tx_ant, self.num_rx_ant, self.num_virtual_ant))
        A("%-22s %d  (res %.4f m, max %.2f m)"
          % ("Range bins", self.num_range_bins, self.range_resolution_m,
             self.max_range_m))
        A("%-22s %d  (bin spacing %.4f m/s, max +-%.3f m/s)"
          % ("Doppler bins", self.num_doppler_bins,
             self.doppler_resolution_mps, self.max_velocity_mps))
        A("%-22s %.4f m/s  (from %d chirps%s)"
          % ("True velocity res", self.doppler_true_resolution_mps,
             self.num_doppler_chirps,
             "; FFT zero-padded to %d bins" % self.num_doppler_bins
             if self.is_zero_padded else ""))
        A("%-22s %.2f us  (idle %.2f + ramp %.2f), ADC window %.2f us"
          % ("Chirp period", self.chirp_period_us, self.idle_time_us,
             self.ramp_end_time_us, self.adc_time_us))
        A("%-22s %d chirps (%d loops x %d Tx), %.2f ms active, %.0f%% duty"
          % ("Frame", self.num_chirps_per_frame, self.num_loops,
             len(self.tx_enable_masks), self.active_frame_time_ms,
             100 * self.duty_cycle))
        A("%-22s %.0f KB / %.0f KB L3"
          % ("Radar cube", self.radar_cube_bytes / 1024, L3_BUDGET_BYTES / 1024))
        A("%-22s %.1f ms (%.2f fps)"
          % ("Frame period", self.frame_periodicity_ms,
             1000.0 / self.frame_periodicity_ms))
        for w in self.warnings():
            A("  ! %s" % w)
        for e in self.check():
            A("  X %s" % e)
        return "\n".join(L)

    # ---- emit ------------------------------------------------------------

    def to_cfg(self) -> str:
        rng_max = (self.range_fov_max_m if self.range_fov_max_m is not None
                   else round(self.max_range_m, 2))
        dop = (self.doppler_fov_m_s if self.doppler_fov_m_s is not None
               else round(self.max_velocity_mps, 2))
        tx_mask = 0
        for m in self.tx_enable_masks:
            tx_mask |= m

        L = [
            # NOTE: these are cfg comment lines, so the leading '%' is literal
            # and must be escaped as '%%' wherever the string is also a format.
            "% ***************************************************************",
            "%% Generated by mmwave_suite.extraction.design -- preset: %s" % self.name,
            "%% %s" % self.description,
            "% Created for SDK ver:03.06",
            "% Platform:xWR68xx_AOP",
            "% Frequency:60",
            "%% Range Resolution(m):%.4f" % self.range_resolution_m,
            "%% Maximum unambiguous Range(m):%.2f" % self.max_range_m,
            "%% Maximum Radial Velocity(m/s):%.3f" % self.max_velocity_mps,
            "%% Radial velocity resolution(m/s):%.4f" % self.doppler_resolution_mps,
            "%% Doppler bins:%d   Range bins:%d" % (self.num_doppler_bins,
                                                    self.num_range_bins),
            "%% Frame Duration(msec):%.0f" % self.frame_periodicity_ms,
            "%% Radar cube:%.0f KB" % (self.radar_cube_bytes / 1024),
            "% ***************************************************************",
            "sensorStop",
            "flushCfg",
            "dfeDataOutputMode 1",
            "channelCfg %d %d 0" % (self.rx_channel_en, tx_mask),
            "adcCfg 2 1",
            "adcbufCfg -1 0 1 1 1",
            "profileCfg 0 %g %g %g %g 0 0 %g %g %d %d 0 0 %d"
            % (self.start_freq_ghz, self.idle_time_us, self.adc_start_time_us,
               self.ramp_end_time_us, self.freq_slope_mhz_per_us,
               self.tx_start_time_us, self.num_adc_samples,
               self.dig_out_sample_rate_ksps, self.rx_gain_db),
        ]
        for i, m in enumerate(self.tx_enable_masks):
            L.append("chirpCfg %d %d 0 0 0 0 0 %d" % (i, i, m))
        L += [
            "frameCfg 0 %d %d 0 %g 1 0"
            % (len(self.tx_enable_masks) - 1, self.num_loops,
               self.frame_periodicity_ms),
            "lowPower 0 0",
            "guiMonitor -1 %d %d %d %d %d %d"
            % (self.gui_detected_objects, self.gui_log_mag_range,
               self.gui_noise_profile, self.gui_range_azimuth_heatmap,
               self.gui_range_doppler_heatmap, self.gui_stats),
            "cfarCfg -1 0 2 8 4 3 0 %g 1" % self.cfar_range_thr_db,
            "cfarCfg -1 1 0 4 2 3 1 %g 1" % self.cfar_doppler_thr_db,
            "multiObjBeamForming -1 %d 0.5" % self.multi_obj_beamforming,
            "clutterRemoval -1 %d" % self.clutter_removal,
            "calibDcRangeSig -1 0 -5 8 256",
            "extendedMaxVelocity -1 %d" % self.extended_max_velocity,
            "lvdsStreamCfg -1 0 0 0",
            "compRangeBiasAndRxChanPhase 0.0 "
            + " ".join(["1 0 -1 0"] * 6),
            "measureRangeBiasAndRxChanPhase 0 1.5 0.2",
            "CQRxSatMonitor 0 3 5 121 0",
            "CQSigImgMonitor 0 127 4",
            "analogMonitor 0 0",
            "aoaFovCfg -1 -90 90 -90 90",
            "cfarFovCfg -1 0 0 %g" % rng_max,
            "cfarFovCfg -1 1 -%g %g" % (dop, dop),
            "calibData 0 0 0",
            "sensorStart",
        ]
        return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


def preset_baseline() -> ChirpDesign:
    """The user's current best_range_res config, for comparison."""
    return ChirpDesign(
        name="baseline",
        description="TI best_range_res preset as currently used",
        idle_time_us=359.0, ramp_end_time_us=57.14, num_adc_samples=256,
        num_loops=16, frame_periodicity_ms=100.0)


def preset_microdoppler() -> ChirpDesign:
    """Doppler-optimised: trades range resolution for velocity span and bins.

    Halving numAdcSamples does three things at once: it halves the ADC window
    (so the ramp, and therefore Tc, can be much shorter -> higher v_max), it
    halves numRangeBins (freeing radar-cube memory -> more Doppler bins), and
    it costs range resolution, which matters least for micro-Doppler.
    """
    return ChirpDesign(
        name="microdoppler",
        description="Doppler-optimised for micro-Doppler of human motion",
        idle_time_us=69.0,
        adc_start_time_us=7.0,
        ramp_end_time_us=32.0,
        num_adc_samples=128,
        dig_out_sample_rate_ksps=5209,
        num_loops=64,
        frame_periodicity_ms=100.0,
        doppler_fov_m_s=None)


def preset_microdoppler_fine() -> ChirpDesign:
    """Same span as `microdoppler`, twice the Doppler bins.

    Doubles numLoops, which doubles both the radar cube and the active frame
    time.  The cube lands exactly on the assumed 768 KB L3 budget, so this is
    the preset most likely to be rejected by the device.  If sensorStart
    reports a memory error, use `gesture` instead -- it reaches the same
    velocity resolution in half the memory by spending range bins.
    """
    return ChirpDesign(
        name="microdoppler-fine",
        description="Finest velocity resolution at full range resolution; "
                    "radar cube is at the L3 limit",
        idle_time_us=69.0,
        adc_start_time_us=7.0,
        ramp_end_time_us=32.0,
        num_adc_samples=128,
        num_loops=128,
        frame_periodicity_ms=100.0)


def preset_microdoppler_fast() -> ChirpDesign:
    """`microdoppler` at 25 fps.

    Identical radar processing -- same span, same resolution, same cube -- but
    2.5x the time resolution on the micro-Doppler spectrogram.  The baseline
    config leaves the radar idle 80% of the time; this spends that idle time on
    the one axis micro-Doppler actually needs.
    """
    return ChirpDesign(
        name="microdoppler-fast",
        description="Same Doppler as `microdoppler`, 25 fps for spectrogram "
                    "time resolution",
        idle_time_us=69.0,
        adc_start_time_us=7.0,
        ramp_end_time_us=32.0,
        num_adc_samples=128,
        num_loops=64,
        frame_periodicity_ms=40.0)


def preset_limb_separation() -> ChirpDesign:
    """Separating hand from torso from leg within one frame.

    Keeps 128 range bins (8.7 cm), because limbs are separated in RANGE as well
    as velocity -- an extended arm sits 30-60 cm ahead of the torso, and joint
    range-Doppler separation is far more robust than Doppler alone.

    Uses 96 Doppler chirps rather than 128: the FFT still produces 128 bins
    (zero-padded), so peak location is interpolated finely, while the radar cube
    drops from 768 KB to a safe 576 KB.  True velocity resolution is CPI-limited
    at 96 chirps -- see doppler_true_resolution_mps.
    """
    return ChirpDesign(
        name="limb-separation",
        description="Hand vs torso vs leg: joint range-Doppler separation, "
                    "memory-safe",
        idle_time_us=69.0,
        adc_start_time_us=7.0,
        ramp_end_time_us=32.0,
        num_adc_samples=128,
        num_loops=96,
        frame_periodicity_ms=50.0)


def preset_balanced() -> ChirpDesign:
    """Keeps the full 256 range bins; buys what Doppler the cube allows."""
    return ChirpDesign(
        name="balanced",
        description="Full range resolution, modest Doppler improvement",
        idle_time_us=143.0,
        ramp_end_time_us=57.14,
        num_adc_samples=256,
        num_loops=32,
        frame_periodicity_ms=100.0)


PRESETS = {
    "baseline": preset_baseline,
    "balanced": preset_balanced,
    "microdoppler": preset_microdoppler,
    "microdoppler-fast": preset_microdoppler_fast,
    "microdoppler-fine": preset_microdoppler_fine,
    "limb-separation": preset_limb_separation,
}


def compare() -> str:
    rows = []
    hdr = ("%-18s %6s %8s %6s %7s %8s %8s %5s %6s %5s"
           % ("preset", "rngbin", "rng res", "dopbin", "v_max", "v_res",
              "v_TRUE", "fps", "cube", "duty"))
    rows.append(hdr)
    rows.append("-" * len(hdr))
    for name, f in PRESETS.items():
        d = f()
        pad = "*" if d.is_zero_padded else " "
        rows.append("%-18s %6d %7.4fm %5d%s %+7.2f %8.4f %8.4f %5.0f %5.0fK %4.0f%%"
                    % (name, d.num_range_bins, d.range_resolution_m,
                       d.num_doppler_bins, pad, d.max_velocity_mps,
                       d.doppler_resolution_mps, d.doppler_true_resolution_mps,
                       1000.0 / d.frame_periodicity_ms,
                       d.radar_cube_bytes / 1024, 100 * d.duty_cycle))
        for e in d.check():
            rows.append("    X %s" % e)
    rows.append("")
    rows.append("v_res  = Doppler bin spacing (what TI reports)")
    rows.append("v_TRUE = velocity resolution actually achievable (CPI-limited);")
    rows.append("         this is what governs separating a hand from a torso.")
    rows.append("*      = FFT is zero-padded, so v_res is finer than v_TRUE.")
    return "\n".join(rows)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=sorted(PRESETS), help="preset to emit")
    ap.add_argument("--out", help="write the .cfg here")
    ap.add_argument("--compare", action="store_true", help="table of all presets")
    a = ap.parse_args(argv)

    if a.compare or not a.preset:
        print(compare())
        if not a.preset:
            return 0
        print()

    d = PRESETS[a.preset]()
    print(d.summary())
    problems = d.check()
    if problems:
        print("\nThis design violates a constraint and would likely be rejected.")
        return 1
    if a.out:
        with open(a.out, "w") as f:
            f.write(d.to_cfg())
        print("\nWrote %s" % a.out)
    else:
        print()
        print(d.to_cfg())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
