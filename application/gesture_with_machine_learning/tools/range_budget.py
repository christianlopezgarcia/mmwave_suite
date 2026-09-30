"""Gesture range budget for the IWR6843AOP: how far each .cfg can classify.

Three independent ceilings decide gesture range, and the lowest one wins:

  1. DETECTION  -- is the hand above the noise floor at all?  SNR ~ 1/R^4.
  2. ANGLE      -- a swipe is recognised by how far the target's angle
                   CENTROID moves.  The angular excursion of a fixed-size
                   gesture shrinks as 1/R while the centroid's noise grows as
                   1/sqrt(SNR) ~ R^2, so the angular signal-to-noise of a
                   gesture falls as 1/R^3.  This is usually what binds.
  3. LINK/UART  -- can the chosen TLV set reach the host at the frame rate the
                   gesture needs?  A swipe lasts ~0.5 s; below ~20 fps you are
                   sampling it with ten points.

The angle model is the standard monopulse/CRLB result for a single dominant
scatterer.  An N-element uniform array at lambda/2 has a 3 dB beamwidth of
about 102/N degrees, and the standard deviation of the angle estimate of one
target inside that beam is roughly

    sigma_theta ~ theta_3dB / (1.6 * sqrt(2 * SNR_linear))

Resolution (can I separate two hands?) is NOT what matters here; precision
(where is the one hand?) is.  That is how TI gets away with a two-element
azimuth aperture whose *resolution* is ~57 degrees.

At the SNRs a hand produces inside a metre, that CRLB term is absurdly small
(millidegrees), so on its own it predicts gesture recognition at 5 m, which is
nonsense.  What actually floors angle precision is the residual RX phase error
left after compRangeBiasAndRxChanPhase: about 5 degrees of phase across a
lambda/2 pair maps to ~1.6 degrees of angle, and no amount of SNR removes it.
PHASE_ERR_DEG below sets that floor and it dominates everywhere that matters.
It is also the only term a bigger azimuth aperture actually improves, which is
why the 3TX suite configs beat TI's own 1TX gesture config on swipe range.

Absolute SNR here is good to maybe +/-5 dB -- the RCS of a hand is not a
constant and the AOP's gain varies across its wide FoV.  The RATIOS between
configs, and the scaling laws, are much more trustworthy than the absolute
numbers, and those are what the conclusions rest on.

Not modelled, and all pessimistic: body clutter (your forearm and torso sit
behind the hand with ~70x its RCS), multipath off the desk, and the fact that
a hand is an extended target that smears across several bins as it approaches.

Run:
    python range_budget.py
"""

from __future__ import annotations

import math

C = 2.99792458e8
K_BOLTZ = 1.380649e-23
T0 = 290.0

# --- IWR6843AOP front-end constants -----------------------------------------
# The AOP patch is deliberately broad (wide FoV) and so is low gain compared
# with the ISK's ~9 dBi.
PT_DBM = 12.0          # per-TX output power
G_TX_DBI = 5.5         # AOP element gain
G_RX_DBI = 5.5
NF_DB = 14.0           # RX noise figure at 60 GHz
LOSS_DB = 4.0          # window loss + implementation + HPF ripple

# Radar cross sections at 60 GHz.  A hand is not a sphere; these are the usual
# working values, good to about a factor of three.
RCS_HAND = 0.01        # m^2, whole hand broadside
RCS_FINGER = 0.001     # m^2, a fingertip -- what a twirl actually moves
RCS_PERSON = 0.7       # m^2, torso

# Residual RX/TX phase error left after compRangeBiasAndRxChanPhase.  For a
# lambda/2 ULA of N elements the end-to-end phase is 2*pi*(N-1)*sin(theta)/2,
# so a residual phase error maps to an angle error of phi/(pi*(N-1)) radians.
# A 2-element azimuth pair therefore floors at ~1.6 deg and a 4-element one at
# ~0.5 deg: this is the ONE place where a bigger aperture buys real range,
# because it is a systematic error that averaging over frames cannot remove.
PHASE_ERR_DEG = 5.0
# Residual range-estimate error as a fraction of one range bin, after bias
# calibration.  Sets the floor on how small a push/pull can be resolved.
SIGMA_RANGE_FRAC = 0.10
# Post-FFT SNR a micro-Doppler class needs: not just "detectable" but with its
# sidebands clear of the noise floor.
MICRODOPPLER_SNR_DB = 20.0

SWIPE_WIDTH_M = 0.30   # lateral excursion of a swipe gesture
ANGLE_SNR_REQUIRED = 10.0   # excursion / sigma needed to classify reliably

# What each gesture class actually asks the radar to measure.
#   kind "angle" -- the target's angle centroid must traverse `extent` metres
#                   laterally, and that traverse must be many sigma_theta wide.
#   kind "range" -- the target's range centroid must traverse `extent` metres
#                   radially, measured against sigma_range.
#   kind "doppler" -- there is no geometric excursion; the class lives in the
#                   micro-Doppler signature, so it is purely SNR-limited on a
#                   fingertip-sized scatterer.  `extent` is unused.
GESTURES = [
    ("swipe L2R/R2L/U2D/D2U", "angle", 0.30),
    ("push/pull (on/off)", "range", 0.20),
    ("twirl CW/CCW", "angle", 0.04),
    ("shine (micro-Doppler)", "doppler", 0.0),
]


def db(x):
    return 10.0 * math.log10(x)


def undb(x):
    return 10.0 ** (x / 10.0)


class Cfg:
    def __init__(self, name, start_ghz, idle_us, adc_start_us, ramp_us,
                 slope_mhz_us, n_adc, fs_ksps, n_tx, n_rx, n_loops,
                 frame_ms, n_tx_azim=None, tlvs="points", note=""):
        self.name = name
        self.n_adc = n_adc
        self.fs = fs_ksps * 1e3
        self.n_tx = n_tx
        self.n_rx = n_rx
        self.n_loops = n_loops
        self.frame_ms = frame_ms
        self.tlvs = tlvs
        self.note = note

        self.t_adc = n_adc / self.fs                       # s
        self.bw = slope_mhz_us * 1e6 * (self.t_adc * 1e6)  # Hz swept during ADC
        self.range_res = C / (2 * self.bw)
        self.n_range_bins = 1 << (n_adc - 1).bit_length()
        self.max_range = self.n_range_bins * self.range_res
        self.fc = start_ghz * 1e9 + self.bw / 2.0
        self.lam = C / self.fc

        self.tc = (idle_us + ramp_us) * 1e-6               # one chirp
        self.tc_eff = self.tc * n_tx                       # TDM-MIMO repeat
        self.n_dopp = 1 << (n_loops - 1).bit_length()
        # CPI-limited resolution, from the number of CHIRPS -- not the bin
        # spacing lam/(2*n_dopp*tc_eff), which a zero-padded FFT makes look
        # finer than it is.  cfgparse reports the spacing; these differ
        # whenever n_loops is not a power of two (e.g. the 96-loop configs).
        self.v_res = self.lam / (2 * n_loops * self.tc_eff)
        self.v_bin = self.lam / (2 * self.n_dopp * self.tc_eff)
        self.v_max = self.lam / (4 * self.tc_eff)
        self.fps = 1000.0 / frame_ms
        self.duty = (n_tx * n_loops * self.tc) / (frame_ms * 1e-3)

        self.n_virt = n_tx * n_rx
        self.n_tx_azim = n_tx_azim if n_tx_azim is not None else min(n_tx, 2)
        # AOP: 4 RX in a 2x2 grid.  Azimuth aperture = 2 RX * n_tx_azim.
        self.n_az_elem = 2 * self.n_tx_azim
        self.theta_az_3db = math.radians(102.0 / self.n_az_elem)
        # phi / (pi * (N-1)) radians, see PHASE_ERR_DEG
        self.sigma_cal_deg = (PHASE_ERR_DEG / math.pi
                              / max(1, self.n_az_elem - 1))

    # -- budgets ------------------------------------------------------------

    def snr_db_at(self, r_m, rcs):
        """Post range-FFT, post Doppler-FFT, post antenna-combining SNR."""
        pt = undb(PT_DBM) * 1e-3
        g = undb(G_TX_DBI) * undb(G_RX_DBI)
        pr = (pt * g * self.lam ** 2 * rcs) / ((4 * math.pi) ** 3 * r_m ** 4)
        noise = K_BOLTZ * T0 * undb(NF_DB) * self.fs
        snr_adc = pr / noise
        gain = self.n_adc * self.n_loops * self.n_virt   # range x Doppler x array
        return db(snr_adc * gain) - LOSS_DB

    def sigma_theta_deg(self, r_m, rcs):
        """Angle-estimate sigma: CRLB in quadrature with the calibration floor."""
        snr = undb(self.snr_db_at(r_m, rcs))
        if snr <= 0:
            return float("inf")
        crlb = math.degrees(self.theta_az_3db / (1.6 * math.sqrt(2 * snr)))
        return math.hypot(crlb, self.sigma_cal_deg)

    def sigma_range_m(self, r_m, rcs):
        snr = undb(self.snr_db_at(r_m, rcs))
        if snr <= 0:
            return float("inf")
        crlb = self.range_res / (1.6 * math.sqrt(2 * snr))
        return math.hypot(crlb, SIGMA_RANGE_FRAC * self.range_res)

    def angle_snr(self, r_m, rcs=RCS_HAND, width=SWIPE_WIDTH_M):
        """How many angle-noise sigmas wide a lateral gesture looks at range r."""
        excursion = math.degrees(2 * math.atan(width / (2 * r_m)))
        return excursion / self.sigma_theta_deg(r_m, rcs)

    def range_snr(self, r_m, rcs=RCS_HAND, depth=0.20):
        """How many range-noise sigmas deep a radial gesture is.  Range
        precision does not degrade with R the way angle does -- only through
        SNR -- which is why push/pull outlives swipe outlives twirl."""
        return depth / self.sigma_range_m(r_m, rcs)

    def gesture_snr(self, name, kind, extent, r_m, rcs=RCS_HAND):
        if kind == "angle":
            return self.angle_snr(r_m, rcs, extent)
        if kind == "doppler":
            # Range-independent in form; SNR-limited in practice.  Express it
            # on the same "sigmas" axis so one threshold compares all classes.
            margin = self.snr_db_at(r_m, RCS_FINGER) - MICRODOPPLER_SNR_DB
            return ANGLE_SNR_REQUIRED * undb(margin / 10.0)
        return self.range_snr(r_m, rcs, extent)

    def max_range_for(self, predicate, lo=0.05, hi=12.0):
        """Largest r in [lo, hi] where predicate holds (monotone decreasing)."""
        if not predicate(lo):
            return 0.0
        if predicate(hi):
            return hi
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if predicate(mid):
                lo = mid
            else:
                hi = mid
        return lo

    def uart_bytes_per_frame(self, n_points=40):
        b = 40  # frame header
        if "points" in self.tlvs:
            b += 8 + 16 * n_points        # TLV1 detected points
            b += 8 + 4 * n_points         # TLV7 side info
        if "rd" in self.tlvs:
            b += 8 + self.n_range_bins * self.n_dopp * 2   # TLV5
        if "azel" in self.tlvs:
            b += 8 + self.n_tx * self.n_rx * self.n_range_bins * 4  # TLV8
        b += 8 + 24                       # TLV6 stats
        return 32 * ((b + 31) // 32)

    def max_fps_on_uart(self, baud=921600, n_points=40):
        return (baud / 10.0) / self.uart_bytes_per_frame(n_points)

    def uart_load(self, baud=921600, n_points=40):
        return self.uart_bytes_per_frame(n_points) * self.fps / (baud / 10.0)


CONFIGS = [
    # TI's own gesture firmware (cli.c:85-115, hard-coded, not sendable to OOB)
    Cfg("TI gesture_ML_6443_AOP", 60.50001, 166, 6, 34, 102.908, 64, 2350,
        n_tx=1, n_rx=4, n_loops=128, frame_ms=35, n_tx_azim=1,
        tlvs="onchip", note="on-chip features+ANN"),

    Cfg("suite ti-baseline_10fps_points", 60, 359, 7, 57.14, 70, 256, 5209,
        n_tx=3, n_rx=4, n_loops=16, frame_ms=100, n_tx_azim=2),
    Cfg("suite gait_25fps_points", 60, 69, 7, 32, 70, 128, 5209,
        n_tx=3, n_rx=4, n_loops=64, frame_ms=40, n_tx_azim=2),
    Cfg("suite ghost_10fps_tlv8", 60, 69, 7, 32, 70, 128, 5209,
        n_tx=3, n_rx=4, n_loops=96, frame_ms=100, n_tx_azim=2,
        tlvs="points+azel"),
    Cfg("suite doppler_8fps_tlv5", 60, 82, 7, 20, 70, 64, 5209,
        n_tx=3, n_rx=4, n_loops=64, frame_ms=125, n_tx_azim=2,
        tlvs="points+rd"),
    Cfg("suite survey_5fps_tlv5+8", 60, 82, 7, 20, 70, 64, 5209,
        n_tx=3, n_rx=4, n_loops=64, frame_ms=200, n_tx_azim=2,
        tlvs="points+rd+azel"),

    # Proposed, shipped in cfg/
    Cfg("NEW gesture_points_30fps", 60, 30, 7, 36, 100, 64, 2500,
        n_tx=3, n_rx=4, n_loops=96, frame_ms=33.33, n_tx_azim=2,
        note="OOB fw, host features from point cloud"),
    Cfg("NEW gesture_rdmap_8fps", 60, 130, 7, 36, 100, 32, 1250,
        n_tx=1, n_rx=4, n_loops=128, frame_ms=125, n_tx_azim=1,
        tlvs="points+rd", note="OOB fw, host features from TLV5"),
]


def main():
    line = "=" * 104
    print(line)
    print("RADAR GEOMETRY")
    print(line)
    print("%-32s %7s %8s %7s %7s %7s %6s %6s %6s"
          % ("config", "dR(cm)", "Rmax(m)", "dV*", "Vmax", "fps", "Ndopp",
             "Nvirt", "duty"))
    for c in CONFIGS:
        print("%-32s %7.1f %8.2f %7.3f %7.2f %7.1f %6d %6d %5.0f%%"
              % (c.name, c.range_res * 100, c.max_range, c.v_res, c.v_max,
                 c.fps, c.n_dopp, c.n_virt, c.duty * 100))
    print("  * dV is CPI-limited resolution (from chirp count). Bin spacing is")
    print("    finer wherever the Doppler FFT is zero-padded; cfgparse reports")
    print("    the spacing, so the two disagree on the 96-loop configs.")

    rs = [0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 4.0]
    print()
    print(line)
    print("SNR vs RANGE (dB) -- hand, RCS = %.2f m2" % RCS_HAND)
    print(line)
    print("%-32s %s" % ("config", " ".join("%7s" % ("%.1fm" % r) for r in rs)))
    for c in CONFIGS:
        print("%-32s %s"
              % (c.name, " ".join("%7.1f" % c.snr_db_at(r, RCS_HAND) for r in rs)))

    print()
    print(line)
    print("MEASUREMENT PRECISION vs RANGE (calibration floor included)")
    print(line)
    print("%-32s %6s %s"
          % ("config", "az3dB", " ".join("%7s" % ("%.1fm" % r) for r in rs)))
    for c in CONFIGS:
        print("%-32s %5.0fd %s   sigma_theta (deg)"
              % (c.name, math.degrees(c.theta_az_3db),
                 " ".join("%7.2f" % c.sigma_theta_deg(r, RCS_HAND) for r in rs)))

    print()
    print(line)
    print("MAX RANGE PER GESTURE CLASS (m) -- gesture extent >= %.0f sigma"
          % ANGLE_SNR_REQUIRED)
    print(line)
    hdr = "%-32s" % "config"
    for name, _k, ext in GESTURES:
        label = name.split()[0] + (" %.0fcm" % (ext * 100) if ext else "")
        hdr += " %18s" % label
    print(hdr)
    for c in CONFIGS:
        row = "%-32s" % c.name
        for name, kind, ext in GESTURES:
            r = c.max_range_for(
                lambda x, k=kind, e=ext: c.gesture_snr(name, k, e, x)
                >= ANGLE_SNR_REQUIRED)
            row += " %18.2f" % r
        print(row)

    print()
    print(line)
    print("CEILINGS (metres)")
    print(line)
    print("%-32s %10s %11s %10s %9s %10s"
          % ("config", "detect15dB", "finger15dB", "swipeOK", "uartFPS",
             "VERDICT"))
    for c in CONFIGS:
        r_det = c.max_range_for(lambda r: c.snr_db_at(r, RCS_HAND) >= 15.0)
        r_fin = c.max_range_for(lambda r: c.snr_db_at(r, RCS_FINGER) >= 15.0)
        r_ang = c.max_range_for(lambda r: c.angle_snr(r) >= ANGLE_SNR_REQUIRED)
        fps = 999.0 if c.tlvs == "onchip" else min(c.max_fps_on_uart(), 999.0)
        print("%-32s %10.2f %11.2f %10.2f %9.1f %10.2f"
              % (c.name, r_det, r_fin, r_ang, fps, min(r_det, r_ang)))

    print()
    print(line)
    print("UART BUDGET, 40 points/frame.  8N1 -> 10 bits/byte.")
    print("  921600 baud =  92160 B/s   (what every capture so far has used)")
    print(" 3125000 baud = 312500 B/s   (MMWDEMO_DATAUART_MAX_BAUDRATE_SUPPORTED,")
    print("                              link.py:53 -- reachable with")
    print("                              configDataPort, UNVERIFIED on this")
    print("                              EVM.  Run tools/uart_probe.py.)")
    print(line)
    print("%-32s %14s %9s %7s %8s %9s %9s"
          % ("config", "TLVs", "B/frame", "fps", "load@921k", "max@921k",
             "max@3.1M"))
    for c in CONFIGS:
        if c.tlvs == "onchip":
            print("%-32s %14s %9d %7.1f %7.0f%% %9s %9s"
                  % (c.name, "1050+1051", 128, c.fps,
                     128 * c.fps / 92160 * 100, "n/a", "n/a"))
            continue
        print("%-32s %14s %9d %7.1f %7.0f%% %9.1f %9.1f"
              % (c.name, c.tlvs, c.uart_bytes_per_frame(), c.fps,
                 c.uart_load() * 100, c.max_fps_on_uart(),
                 c.max_fps_on_uart(baud=3125000)))

    ti = CONFIGS[0]
    print()
    print("TI's trained operating point (model/ti_ann_6843.json):")
    print("  weightedRange mean 4.39 bins = %.3f m, std 0.667 bins = %.1f cm."
          % (4.39 * ti.range_res, 0.667 * ti.range_res * 100))
    print("  The network only ever saw the hand at 23.5 +/- 3.6 cm.")
    print("  Doppler bins 0..5 and 123..127 are zeroed -> everything slower")
    print("  than %.2f m/s is discarded before features are computed."
          % (5 * ti.v_res))


if __name__ == "__main__":
    main()
