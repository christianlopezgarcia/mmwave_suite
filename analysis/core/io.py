"""
core/io.py -- load a TI .dat + .cfg pair into a Recording.

Parsing is delegated to mmwave_suite.extraction, a verified port of TI's
own JavaScript decoder (see ../../extraction/ARCHITECTURE.md). Nothing here
re-implements TLV decoding; this module only reshapes the result into the
analysis-facing `Recording`.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

import numpy as np

from .recording import Geometry, OnChipGating, Recording


def _parser():
    """Return the extraction package that owns TLV decoding.

    `extraction/` is a sibling inside mmwave_suite, so this is an ordinary
    relative import. The fallback exists only for the older layout where the
    parser was a standalone `mmwave_direct` package.
    """
    # io.py is mmwave_suite.analysis.core.io, so the suite root is three levels
    # up: '.' = core, '..' = analysis, '...' = mmwave_suite.
    first_error = None
    try:
        from ... import extraction
        return extraction
    except ImportError as e:
        first_error = e
    try:
        import mmwave_direct                   # legacy standalone layout
        return mmwave_direct
    except ImportError:
        raise ImportError(
            "Could not import the TLV parser from mmwave_suite/extraction/.\n"
            "  relative import failed: %s\n"
            "  and no legacy standalone `mmwave_direct` on sys.path.\n"
            "Run from the directory containing mmwave_suite/, e.g.\n"
            "  python -m mmwave_suite.analysis.pipelines.run ..." % first_error)


# Configs to fall back on when a recording has no .cfg beside it, in order of
# preference. Relative to extraction/cfg/.
#
# A fallback is a LAST RESORT and is always announced loudly: the .cfg sets
# range_idx_to_meters and doppler_resolution_mps, so the wrong one silently
# rescales every axis in every figure. Wrong-but-plotted is worse than refusing.
FALLBACK_CFGS = (
    "xwr68xx_AOP_10fps.cfg",              # the config used for most captures
    "xwr68xx_AOP_limb-separation.cfg",
    "xwr68xx_AOP_microdoppler.cfg",
)


def _fallback_cfg() -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    cfg_dir = os.path.normpath(os.path.join(here, "..", "..", "extraction", "cfg"))
    for name in FALLBACK_CFGS:
        p = os.path.join(cfg_dir, name)
        if os.path.isfile(p):
            return p
    return None


def _check_exists(path: str, what: str) -> None:
    """Fail early and legibly on a bad path.

    Without this the first failure surfaces deep inside directory listing, and
    the traceback points at the config-discovery code rather than at the
    argument the user actually got wrong.
    """
    if os.path.isfile(path):
        return

    msg = ["Cannot find the %s: %s" % (what, path)]
    ab = os.path.abspath(path)
    if ab != os.path.normpath(path):
        msg.append("  resolved to: %s" % ab)

    # A doubled directory component almost always means the path was given
    # relative to the wrong working directory.
    parts = [p for p in os.path.normpath(ab).split(os.sep) if p]
    for i in range(len(parts) - 1):
        if parts[i] and parts[i] == parts[i + 1]:
            msg.append("  NOTE: %r appears twice in a row. You are probably "
                       "already inside %r -- drop it from the path."
                       % (parts[i], parts[i]))
            break

    d = os.path.dirname(ab)
    while d and not os.path.isdir(d) and len(d) > 3:
        d = os.path.dirname(d)
    if d and os.path.isdir(d):
        try:
            entries = sorted(os.listdir(d))[:12]
            msg.append("  nearest existing directory: %s" % d)
            if entries:
                msg.append("    contains: %s" % ", ".join(entries))
        except OSError:
            pass
    msg.append("  cwd: %s" % os.getcwd())
    raise FileNotFoundError("\n".join(msg))


def load_recording(dat_path: str,
                   cfg_path: Optional[str] = None,
                   name: Optional[str] = None) -> Recording:
    """Load one capture.

    If `cfg_path` is omitted, looks for a single .cfg beside the .dat -- which is
    how both the TI Visualizer and extraction.live lay runs out. Pairing the
    wrong .cfg silently rescales every axis, so this refuses to guess when the
    choice is ambiguous.
    """
    ex = _parser()
    parse_cfg, parse_dat = ex.parse_cfg, ex.parse_dat

    _check_exists(dat_path, ".dat recording")

    cfg_is_fallback = False
    if cfg_path is None:
        d = os.path.dirname(os.path.abspath(dat_path))
        cands = [os.path.join(d, f) for f in sorted(os.listdir(d))
                 if f.lower().endswith(".cfg")]
        if len(cands) == 1:
            cfg_path = cands[0]
        elif len(cands) > 1:
            raise FileNotFoundError(
                "Found %d .cfg files beside %s:\n  %s\nPass cfg_path explicitly "
                "-- a mismatched .cfg silently rescales every axis, and this is "
                "not something to guess at."
                % (len(cands), dat_path, "\n  ".join(map(os.path.basename, cands))))
        else:
            cfg_path = _fallback_cfg()
            if cfg_path is None:
                raise FileNotFoundError(
                    "No .cfg beside %s and no fallback config available.\n"
                    "Pass cfg_path explicitly." % dat_path)
            cfg_is_fallback = True
            print("=" * 74)
            print("WARNING: no .cfg found beside this recording.")
            print("Falling back to: %s" % os.path.basename(cfg_path))
            print("The .cfg sets range_idx_to_meters and doppler_resolution_mps.")
            print("If it does not match the capture, EVERY range and velocity")
            print("number below is wrong by a constant factor -- the plots will")
            print("still look plausible. Verify with:")
            print("  python -m mmwave_suite.extraction.audit <dat> --cfg <cfg>")
            print("and check 'Range-bin cross-check' reports MATCH.")
            print("=" * 74)
    else:
        _check_exists(cfg_path, ".cfg config")

    cfg = parse_cfg(cfg_path)
    frames, stats = parse_dat(dat_path, geometry=cfg.geometry())
    if not frames:
        raise ValueError("No frames parsed from %s" % dat_path)

    geom = Geometry(
        num_range_bins=cfg.num_range_bins,
        num_doppler_bins=cfg.num_doppler_bins,
        num_doppler_chirps=cfg.num_doppler_chirps,
        range_idx_to_meters=cfg.range_idx_to_meters,
        range_resolution_m=cfg.range_resolution_m,
        doppler_resolution_mps=cfg.doppler_resolution_mps,
        max_velocity_mps=cfg.max_velocity_mps,
        frame_period_s=cfg.frame_periodicity_ms / 1000.0,
        num_tx_ant=cfg.num_tx_ant,
        num_rx_ant=cfg.num_rx_ant,
        range_bias_m=getattr(cfg, "range_bias_m", 0.0),
        platform=cfg.platform,
    )

    f = cfg.filters
    gating = OnChipGating(
        cfar_range_threshold_db=f.cfar_range_threshold_db,
        cfar_doppler_threshold_db=f.cfar_doppler_threshold_db,
        range_fov_m=(f.range_fov_min_m, f.range_fov_max_m)
        if f.range_fov_max_m is not None else None,
        doppler_fov_mps=(f.doppler_fov_min_mps, f.doppler_fov_max_mps)
        if f.doppler_fov_max_mps is not None else None,
        azimuth_fov_deg=(f.azimuth_fov_min_deg, f.azimuth_fov_max_deg)
        if f.azimuth_fov_max_deg is not None else None,
        elevation_fov_deg=(f.elevation_fov_min_deg, f.elevation_fov_max_deg)
        if f.elevation_fov_max_deg is not None else None,
        clutter_removal=f.clutter_removal,
        multi_obj_beamforming=f.multi_obj_beam_forming_enabled,
        multi_obj_threshold=f.multi_obj_beam_forming_threshold,
        extended_max_velocity=f.extended_max_velocity,
    )

    fn = np.array([fr.header.frame_number for fr in frames], dtype=np.int64)
    # Time from the frame counter, not host arrival: deterministic, and immune
    # to jitter in USB delivery. Rebased so a counter reset does not warp t.
    t = (fn - fn[0]).astype(np.float64) * geom.frame_period_s
    if np.any(np.diff(fn) < 0):
        t = np.arange(len(fn), dtype=np.float64) * geom.frame_period_s

    rec = Recording(
        name=name or os.path.splitext(os.path.basename(dat_path))[0],
        path=os.path.abspath(dat_path),
        geom=geom, gating=gating,
        frame_numbers=fn, t=t,
        points=[fr.points for fr in frames],
        side_info=[fr.side_info for fr in frames],
        stats=[fr.stats for fr in frames],
        anomalies=dict(stats.anomaly_counts),
        lost_bytes=stats.bytes_skipped,
        resyncs=stats.resyncs,
    )
    rec.cfg_path = os.path.abspath(cfg_path)
    rec.cfg_is_fallback = cfg_is_fallback

    # Dense range profile (TLV 2) -> dB, using TI's Q-format chain.
    rp = [fr.range_profile for fr in frames]
    if any(x is not None and x.size for x in rp):
        nb = geom.num_range_bins
        M = np.full((len(frames), nb), np.nan)
        for i, x in enumerate(rp):
            if x is not None and x.size:
                M[i, :min(nb, x.size)] = cfg.range_profile_db(x[:nb])
        rec.range_profile = M

    npf = [fr.noise_profile for fr in frames]
    if any(x is not None and x.size for x in npf):
        nb = geom.num_range_bins
        M = np.full((len(frames), nb), np.nan)
        for i, x in enumerate(npf):
            if x is not None and x.size:
                M[i, :min(nb, x.size)] = cfg.range_profile_db(x[:nb])
        rec.noise_profile = M

    # Range-Doppler map (TLV 5), already column-major-corrected by the parser.
    rd = [fr.range_doppler_heatmap for fr in frames]
    if any(x is not None and getattr(x, "ndim", 0) == 2 for x in rd):
        shape = next(x.shape for x in rd if x is not None and x.ndim == 2)
        M = np.full((len(frames),) + shape, np.nan, dtype=np.float32)
        for i, x in enumerate(rd):
            if x is not None and x.ndim == 2 and x.shape == shape:
                M[i] = x
        rec.rd_map = M

    return rec


def find_runs(root: str):
    """Yield (dat_path, cfg_path) for every run directory beneath `root`."""
    for dirpath, _dirnames, filenames in os.walk(root):
        dats = [f for f in filenames if f.lower().endswith(".dat")]
        cfgs = [f for f in filenames if f.lower().endswith(".cfg")]
        if len(dats) == 1 and len(cfgs) == 1:
            yield (os.path.join(dirpath, dats[0]),
                   os.path.join(dirpath, cfgs[0]))
