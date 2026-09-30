"""
highfidelity/configs.py -- the capture presets. Four, bandwidth-checked.

This is the ONLY preset catalogue in the suite. Pick one, write it, capture with
it. Every preset is validated against both the radar-cube memory limit and the
UART budget before it is written: enabling a heat map without shrinking the
geometry pushes the device past the UART's ~92 KB/s and it tears frames, which
looks like a hardware fault rather than a config mistake.

    preset      what it is for                          fps   extra payload
    ------------------------------------------------------------------------
    gait        walking, micro-Doppler, limb motion      25    none
    ghost       multipath / ghost discrimination         10    TLV 8 (angle)
    doppler     dense range-Doppler spectrum              8    TLV 5 (RD map)
    survey      static-scene study, both heat maps        5    TLV 5 + TLV 8

How to choose
-------------

`gait`      Point cloud + range profile only, so the UART is nearly idle and all
            the budget goes into FRAME RATE. 25 fps is ~25 samples per stride,
            which is what a micro-Doppler spectrogram needs to resolve limb
            swing. Start here for anything involving a walking subject.

`ghost`     Adds TLV 8: complex I/Q per virtual antenna, the only payload TI
            ships PRE-FFT. Lets you beamform yourself, which matters because a
            specular ghost is *defined* by arriving from the wrong direction.
            TLV 8 is a zero-Doppler slice, so its size is independent of
            numDopplerBins -- full 128x128 resolution is kept.

`doppler`   Adds TLV 5: the dense range-Doppler spectrum, PRE-CFAR. This is the
            only way to get the paper's Eq. (1) VT map verbatim instead of a
            sparse reconstruction from CFAR points, and the only way to do
            empty-scene background subtraction. But TLV 5 carries NO angle
            information, so it cannot discriminate a ghost -- it only reveals the
            weak multipath CFAR was discarding.

`survey`    Both heat maps at 5 fps. Use for a careful static-scene study. Too
            slow for gait: 5 fps is ~5 samples per stride.

Hard truth: none of this can be retrofitted to an existing recording. If a
payload was not enabled at capture time, the bytes were never transmitted.

A note on frame rate and gait
-----------------------------

Doppler *resolution* comes from the chirps within one frame (numLoops); frame
rate sets how finely you can watch the gait cycle evolve over time. A human
stride is roughly 1 s with limb harmonics well above the fundamental, so 5 fps
cannot resolve it no matter how many Doppler bins the frame has. If gait is the
measurement, frame rate is the parameter that matters -- use `gait`.
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



def preset_gait() -> ChirpDesign:
    """Walking and micro-Doppler. Point cloud only -- spend the link on fps.

    Halving numAdcSamples to 128 shortens the ramp (so Tc drops and v_max rises
    to +-4.07 m/s), halves numRangeBins (freeing cube memory for 64 Doppler
    bins), and costs range resolution -- which matters least for micro-Doppler.
    No heat map, so the UART is nearly idle and the frame period can drop to
    40 ms: 25 fps, ~25 samples per stride.
    """
    return _base(
        name="gait",
        description="Walking / micro-Doppler: 25 fps, point cloud only",
        use_for="a walking subject -- gait, micro-Doppler, limb motion",
        not_for="anything needing angle (no TLV 8) or a dense VT map (no TLV 5)",
        num_adc_samples=128, ramp_end_time_us=32.0, idle_time_us=69.0,
        num_loops=64, frame_periodicity_ms=40.0,
        gui_range_azimuth_heatmap=0, gui_range_doppler_heatmap=0,
    )


def preset_ghost() -> ChirpDesign:
    """Multipath / ghost work. TLV 8 angle I/Q at full 128x128 resolution.

    96 Doppler chirps rather than 128: the FFT still yields 128 bins, so the
    velocity axis is unchanged, but the radar cube drops to 576 KB from 768 KB
    (which sits exactly on the assumed L3 limit).

    Also the preset for an EMPTY-ROOM reference capture -- that is a capture
    protocol, not a different config. Use `--label empty-room`.
    """
    return _base(
        name="ghost",
        description="Multipath/ghost: TLV 8 complex I/Q per virtual antenna",
        use_for="multipath / ghost discrimination; also the empty-room reference",
        not_for="gait metrics -- 10 fps is only ~10 samples per stride",
        num_adc_samples=128, ramp_end_time_us=32.0, idle_time_us=69.0,
        num_loops=96, frame_periodicity_ms=100.0,
        gui_range_azimuth_heatmap=1, gui_range_doppler_heatmap=0,
    )


def preset_doppler() -> ChirpDesign:
    """Dense range-Doppler spectrum (TLV 5). Tier B -- Eq. (1) verbatim.

    64x64 at 8 fps is what the UART allows: a 64x64 RD map is 8 KB/frame, and
    the geometry has to shrink in step with the frame rate. No angle payload.
    """
    return _base(
        name="doppler",
        description="Dense pre-CFAR range-Doppler map (TLV 5) for true RT/VT",
        use_for="dense pre-CFAR range-Doppler spectrum -- the paper's Eq. (1) verbatim",
        not_for="ghost discrimination (no angle data) or gait (8 fps)",
        num_adc_samples=64, ramp_end_time_us=20.0, idle_time_us=82.0,
        num_loops=64, frame_periodicity_ms=125.0,
        gui_range_azimuth_heatmap=0, gui_range_doppler_heatmap=1,
    )


def preset_survey() -> ChirpDesign:
    """Both heat maps. The complete picture, at a low frame rate.

    For a careful static-scene study where you want angle AND the dense
    range-Doppler spectrum from the same capture. Not for gait -- 5 fps is
    roughly 5 samples per stride.
    """
    return _base(
        name="survey",
        description="Both heat maps (TLV 5 + TLV 8), static-scene study, 5 fps",
        use_for="a careful static-scene study needing angle AND range-Doppler at once",
        not_for="anything moving -- 5 fps is ~5 samples per stride",
        num_adc_samples=64, ramp_end_time_us=20.0, idle_time_us=82.0,
        num_loops=64, frame_periodicity_ms=200.0,
        gui_range_azimuth_heatmap=1, gui_range_doppler_heatmap=1,
    )


PRESETS = {
    "gait": preset_gait,
    "ghost": preset_ghost,
    "doppler": preset_doppler,
    "survey": preset_survey,
}


def filename_slug(d: ChirpDesign) -> str:
    """`<purpose>_<fps>fps_<payload>` -- so a directory listing alone tells you
    what a config is for, how fast it runs, and what extra payload it enables.

    Frame rate is in the name deliberately: picking a 5 fps diagnostic for a
    day of walking captures is the exact mistake this is meant to make visible.
    """
    tag = []
    if d.gui_range_doppler_heatmap:
        tag.append("tlv5")
    if d.gui_range_azimuth_heatmap:
        tag.append("tlv8")
    payload = "+".join(tag) if tag else "points"
    return "%s_%gfps_%s" % (d.name, round(1000.0 / d.frame_periodicity_ms, 1),
                            payload)


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
    hdr = ("%-16s %6s %6s %7s %8s %8s %8s %6s %8s %7s %s"
           % ("preset", "rngbin", "dopbin", "fps", "v_res", "v_TRUE",
              "rng res", "cube", "B/s", "util", "TLVs"))
    L = [hdr, "-" * len(hdr)]
    for name, f in PRESETS.items():
        d = f()
        b = budget_for(d)
        tlvs = []
        if d.gui_range_doppler_heatmap:
            tlvs.append("5")
        if d.gui_range_azimuth_heatmap:
            tlvs.append("8")
        L.append("%-16s %6d %6d %7.1f %8.4f %8.4f %8.3f %5.0fK %8.0f %6.0f%% %s"
                 % (name, d.num_range_bins, d.num_doppler_bins,
                    1000.0 / d.frame_periodicity_ms,
                    d.doppler_resolution_mps, d.doppler_true_resolution_mps,
                    d.range_resolution_m,
                    d.radar_cube_bytes / 1024, b.bytes_per_second,
                    100 * b.utilisation,
                    "+".join(tlvs) if tlvs else "none (point cloud only)"))
        for p in validate(d):
            L.append("    X %s" % p)
    L.append("")
    L.append("v_res  = Doppler bin spacing (what TI reports)")
    L.append("v_TRUE = velocity resolution actually achievable (CPI-limited).")
    L.append("         This is what governs separating a hand from a torso; it")
    L.append("         differs from v_res whenever the Doppler FFT is zero-padded.")
    L.append("util   = fraction of the 921600-baud link consumed. Above ~75% the")
    L.append("         link has no slack and frames tear.")
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
        b = budget_for(d)
        p = os.path.join(out_dir, "xwr68xx_AOP_%s.cfg" % filename_slug(d))
        with open(p, "w") as fh:
            fh.write(d.to_cfg(link_note=(
                "UART: %.0f B/s of %.0f B/s usable (%.0f%% of the 921600-baud link)"
                % (b.bytes_per_second, b.budget, 100 * b.utilisation))))
        written[name] = p
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
