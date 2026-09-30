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
    python -m mmwave_suite.extraction.design --preset gait --out my.cfg
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

    # Plain-language labelling, stamped into the .cfg header so the file says
    # what it is for without needing the README open.
    use_for: str = ""
    not_for: str = ""

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

    @property
    def extra_payloads(self) -> str:
        """Human-readable list of the optional heat-map TLVs this enables."""
        t = []
        if self.gui_range_doppler_heatmap:
            t.append("TLV 5 range-Doppler heat map")
        if self.gui_range_azimuth_heatmap:
            t.append("TLV 8 azimuth-elevation angle I/Q")
        return " + ".join(t) if t else "none (point cloud + range profile only)"

    def to_cfg(self, link_note: str = "") -> str:
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
            "%% PRESET         : %s" % self.name,
            "%% WHAT IT IS     : %s" % self.description,
            "%% USE FOR        : %s" % (self.use_for or "-"),
            "%% DO NOT USE FOR : %s" % (self.not_for or "-"),
            "%% FRAME RATE     : %.1f fps  (frame period %.0f ms)"
            % (1000.0 / self.frame_periodicity_ms, self.frame_periodicity_ms),
            "%% EXTRA PAYLOAD  : %s" % self.extra_payloads,
            "% ---------------------------------------------------------------",
            "% Created for SDK ver:03.06",
            "% Platform:xWR68xx_AOP",
            "% Frequency:60",
            "%% Range Resolution(m):%.4f" % self.range_resolution_m,
            "%% Maximum unambiguous Range(m):%.2f" % self.max_range_m,
            "%% Maximum Radial Velocity(m/s):%.3f" % self.max_velocity_mps,
            "%% Radial velocity resolution(m/s):%.4f" % self.doppler_resolution_mps,
            "%% True velocity resolution(m/s):%.4f  (CPI-limited)"
            % self.doppler_true_resolution_mps,
            "%% Doppler bins:%d   Range bins:%d" % (self.num_doppler_bins,
                                                    self.num_range_bins),
            "%% Radar cube:%.0f KB of %.0f KB L3"
            % (self.radar_cube_bytes / 1024, L3_BUDGET_BYTES / 1024),
            "%% %s" % (link_note or "UART budget: not checked at write time"),
            "% ---------------------------------------------------------------",
            "% Generated by mmwave_suite.extraction.highfidelity.configs.",
            "% Do not hand-edit: change the preset and regenerate, so the file",
            "% and the catalogue can never disagree.",
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
#
# The preset CATALOGUE lives in highfidelity/configs.py, not here. This module
# is the chirp MODEL: it computes TI's derived quantities and checks the
# constraints. Keeping one catalogue means a preset cannot be bandwidth-checked
# in one place and unchecked in another, which is how `angle-probe` -- a 5 fps
# diagnostic -- ended up being used for a whole day of production captures.


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", help="preset name (see --compare)")
    ap.add_argument("--out", help="write the .cfg here")
    ap.add_argument("--compare", action="store_true",
                    help="table of all presets, with the UART budget")
    a = ap.parse_args(argv)

    from .highfidelity import configs

    if a.compare or not a.preset:
        print(configs.compare())
        if not a.preset:
            return 0
        print()

    if a.preset not in configs.PRESETS:
        print("unknown preset %r. Available: %s"
              % (a.preset, ", ".join(sorted(configs.PRESETS))))
        return 1

    d = configs.PRESETS[a.preset]()
    print(d.summary())
    print()
    print(configs.budget_for(d).report())
    problems = configs.validate(d)
    if problems:
        print()
        print("This design violates a constraint and would likely be rejected:")
        for prob in problems:
            print("  X %s" % prob)
        return 1
    if a.out:
        with open(a.out, "w") as f:
            f.write(d.to_cfg())
        print()
        print("Wrote %s" % a.out)
    else:
        print()
        print(d.to_cfg())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
