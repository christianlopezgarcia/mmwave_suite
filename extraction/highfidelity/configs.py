"""
highfidelity/configs.py -- configs that turn the heat maps ON, within budget.

Every preset here is bandwidth-checked before it is written. Enabling a heat map
without shrinking the geometry pushes the device past the UART's 92 KB/s and it
tears frames -- which looks like a hardware fault, not a config mistake.

What each unlocks, and why you would pick it
--------------------------------------------

`angle-ghost`   TLV 8 only. **The one for multipath work.**
                Complex I/Q per virtual antenna at zero Doppler -- the only
                payload TI ships PRE-FFT. Lets you do your own beamforming, and
                a specular ghost is *defined* by arriving from the wrong
                direction. Costs nothing in range or Doppler resolution because
                TLV 8 is a zero-Doppler slice: its size is independent of
                numDopplerBins.

`rdmap-*`       TLV 5. Dense range-Doppler, PRE-CFAR.
                Gives the paper's Eq. (1) RT/VT verbatim, and makes empty-scene
                background subtraction possible for the first time. But it has
                NO angle information, so it cannot discriminate a ghost --
                it only reveals the weak multipath that CFAR was discarding.

`both-*`        Both heat maps. The complete picture, at a low frame rate.
                Use for a careful static-scene study, not for gait.

Hard truth: none of this can be retrofitted to existing recordings. If a payload
was not enabled at capture time, the bytes were never transmitted.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from ..design import ChirpDesign, L3_BUDGET_BYTES
from .bandwidth import DEFAULT_BAUD, DEFAULT_SAFETY, check


def _base(**kw) -> ChirpDesign:
    """Shared RF front end: 60 GHz, 70 MHz/us slope, 5209 ksps.

    Held constant across every preset so that differences between captures are
    attributable to the geometry and the enabled payloads, never to the RF.
    """
    d = ChirpDesign(
        start_freq_ghz=60.0, freq_slope_mhz_per_us=70.0,
        dig_out_sample_rate_ksps=5209, adc_start_time_us=7.0,
        rx_channel_en=15, tx_enable_masks=[1, 2, 4],
        gui_detected_objects=1, gui_log_mag_range=1, gui_stats=1,
    )
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def preset_angle_ghost() -> ChirpDesign:
    """TLV 8 azimuth-elevation heat map. Full range/Doppler resolution kept.

    96 Doppler chirps rather than 128: the FFT still yields 128 bins, so the
    velocity axis is unchanged, but the radar cube drops to 589 KB from 768 KB
    (which sits exactly on the assumed L3 limit).
    """
    return _base(
        name="angle-ghost",
        description="TLV 8 complex I/Q per virtual antenna -- for ghost/multipath "
                    "discrimination. Full 128x128 resolution retained.",
        num_adc_samples=128, ramp_end_time_us=32.0, idle_time_us=69.0,
        num_loops=96, frame_periodicity_ms=100.0,
        gui_range_azimuth_heatmap=1, gui_range_doppler_heatmap=0,
    )


def preset_rdmap_balanced() -> ChirpDesign:
    """TLV 5 at 64x64. True Eq.(1) RT/VT and empty-scene subtraction."""
    return _base(
        name="rdmap-balanced",
        description="TLV 5 dense range-Doppler at 64x64 -- pre-CFAR, enables "
                    "Eq.(1) RT/VT and background subtraction.",
        num_adc_samples=64, ramp_end_time_us=20.0, idle_time_us=82.0,
        num_loops=64, frame_periodicity_ms=125.0,
        gui_range_azimuth_heatmap=0, gui_range_doppler_heatmap=1,
    )


def preset_rdmap_fast() -> ChirpDesign:
    """TLV 5 at 64x32, ~15 fps. Coarser velocity, keeps usable time resolution."""
    return _base(
        name="rdmap-fast",
        description="TLV 5 at 64x32, ~15 fps -- coarser velocity grid but "
                    "enough frame rate for gait.",
        num_adc_samples=64, ramp_end_time_us=20.0, idle_time_us=82.0,
        num_loops=32, frame_periodicity_ms=66.0,
        gui_range_azimuth_heatmap=0, gui_range_doppler_heatmap=1,
    )


def preset_both_survey() -> ChirpDesign:
    """Both heat maps. Low frame rate -- for a static or slow-motion study."""
    return _base(
        name="both-survey",
        description="TLV 5 + TLV 8 together. Complete data, low frame rate; "
                    "for careful static-scene work, not gait.",
        num_adc_samples=64, ramp_end_time_us=20.0, idle_time_us=82.0,
        num_loops=64, frame_periodicity_ms=200.0,
        gui_range_azimuth_heatmap=1, gui_range_doppler_heatmap=1,
    )


def preset_empty_room() -> ChirpDesign:
    """Identical to `angle-ghost` -- for the matched empty-scene capture.

    Background subtraction requires the SAME geometry, so this is deliberately
    the same design under a different name. Capture 30 s of the room with nobody
    in it, from the same position. This is the step that cannot be added later.
    """
    d = preset_angle_ghost()
    d.name = "empty-room"
    d.description = ("Empty-scene reference for background subtraction. "
                     "Geometry identical to angle-ghost -- do not change it.")
    return d


PRESETS = {
    "angle-ghost": preset_angle_ghost,
    "rdmap-balanced": preset_rdmap_balanced,
    "rdmap-fast": preset_rdmap_fast,
    "both-survey": preset_both_survey,
    "empty-room": preset_empty_room,
}


def budget_for(d: ChirpDesign, baud: int = DEFAULT_BAUD,
               safety: float = DEFAULT_SAFETY):
    return check(
        num_range_bins=d.num_range_bins,
        num_doppler_bins=d.num_doppler_bins,
        fps=1000.0 / d.frame_periodicity_ms,
        num_virtual_ant=d.num_virtual_ant,
        baud=baud, safety=safety,
        num_detected_obj=8,
        detected_objects=d.gui_detected_objects,
        log_mag_range=d.gui_log_mag_range,
        noise_profile=d.gui_noise_profile,
        range_azimuth_heatmap=d.gui_range_azimuth_heatmap,
        range_doppler_heatmap=d.gui_range_doppler_heatmap,
        stats_info=d.gui_stats,
        aop=True,
    )


def validate(d: ChirpDesign) -> List[str]:
    """Chirp-level constraints AND the UART budget. Both must pass."""
    problems = list(d.check())
    b = budget_for(d)
    if not b.fits:
        problems.append(
            "UART over budget: %.0f B/s needed, %.0f B/s usable. Max %.1f fps "
            "at this geometry (currently %.1f)."
            % (b.bytes_per_second, b.budget, b.max_fps,
               1000.0 / d.frame_periodicity_ms))
    return problems


def compare() -> str:
    hdr = ("%-16s %6s %6s %7s %8s %8s %6s %8s %7s %s"
           % ("preset", "rngbin", "dopbin", "fps", "v_res", "rng res",
              "cube", "B/s", "util", "TLVs"))
    L = [hdr, "-" * len(hdr)]
    for name, f in PRESETS.items():
        d = f()
        b = budget_for(d)
        tlvs = []
        if d.gui_range_doppler_heatmap:
            tlvs.append("5")
        if d.gui_range_azimuth_heatmap:
            tlvs.append("8")
        L.append("%-16s %6d %6d %7.1f %8.4f %8.3f %5.0fK %8.0f %6.0f%% %s"
                 % (name, d.num_range_bins, d.num_doppler_bins,
                    1000.0 / d.frame_periodicity_ms,
                    d.doppler_resolution_mps, d.range_resolution_m,
                    d.radar_cube_bytes / 1024, b.bytes_per_second,
                    100 * b.utilisation,
                    "+".join(tlvs) if tlvs else "none (point cloud only)"))
        for p in validate(d):
            L.append("    X %s" % p)
    L.append("")
    L.append("util = fraction of the 921600-baud link consumed. Above ~75%% the")
    L.append("link has no slack and frames tear.")
    return "\n".join(L)


def write_all(out_dir: str) -> Dict[str, str]:
    """Write every valid preset. Refuses to write one that fails validation."""
    os.makedirs(out_dir, exist_ok=True)
    written: Dict[str, str] = {}
    for name, f in PRESETS.items():
        d = f()
        problems = validate(d)
        if problems:
            print("SKIP %-16s %s" % (name, problems[0]))
            continue
        p = os.path.join(out_dir, "xwr68xx_AOP_%s.cfg" % name)
        with open(p, "w") as fh:
            fh.write(d.to_cfg())
        written[name] = p
        b = budget_for(d)
        print("wrote %-42s %5.1f fps, %.0f%% of link"
              % (os.path.basename(p), 1000.0 / d.frame_periodicity_ms,
                 100 * b.utilisation))
    return written


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--out", help="write .cfg here (or a directory with --all)")
    ap.add_argument("--all", action="store_true", help="write every preset")
    a = ap.parse_args()

    if a.all:
        print(compare())
        print()
        write_all(a.out or "cfg")
    elif a.preset:
        d = PRESETS[a.preset]()
        print(d.summary())
        print()
        print(budget_for(d).report())
        problems = validate(d)
        if problems:
            print("\nINVALID:")
            for p in problems:
                print("  X %s" % p)
            raise SystemExit(1)
        if a.out:
            with open(a.out, "w") as fh:
                fh.write(d.to_cfg())
            print("\nWrote %s" % a.out)
    else:
        print(compare())
