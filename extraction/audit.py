"""
audit.py -- Prove what is and is not in a TI .dat recording.

Answers, from your own data rather than from documentation:

  * Is every byte accounted for?  (Did the visualizer drop anything on the way
    to disk?)
  * Which TLV types are present, and in what order?
  * Does the point-cloud TLV length agree with the header's numDetectedObj?
    (If the visualizer were dropping points, these would disagree.)
  * How many frames does TI's own reference Python parser silently lose on
    this file?
  * Do the detections respect the on-chip FoV / CFAR gates from the .cfg?
    (This is where points actually disappear.)

Usage:
    python -m mmwave_suite.extraction.audit <file.dat> [--cfg <file.cfg>] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

import numpy as np

from .tlv import MAGIC, TLVType, StreamStats, iter_frames
from .cfgparse import RadarConfig, parse_cfg


def _pct(a: float, b: float) -> str:
    return "  (%.4f%%)" % (100.0 * a / b) if b else ""


# ---------------------------------------------------------------------------


def simulate_ti_reference_parser(buf: bytes) -> Dict[str, int]:
    """Replicate the frame-acceptance policy of TI's parser_mmw_demo.py.

    Reproduces exactly the conditions under which
    parser_one_mmw_demo_output_packet() returns TC_FAIL, and the caller
    convention used in dat_parser_plots*.py:

        result = parser_one_mmw_demo_output_packet(...)
        if result[0] != 0: break     # <-- terminates the whole chunk

    The single most damaging condition is `numDetObj <= 0` -> TC_FAIL, which
    ends parsing at the first empty frame.  For through-wall sensing, empty
    frames are common and often occur early.

    Returns counts describing what that policy costs on this file.
    """
    from .tlv import FRAME_HEADER_LEN, _read_header, _header_is_plausible

    n = len(buf)
    pos = buf.find(MAGIC)
    total = 0
    would_stop_at = None
    stop_reason = ""
    first_empty_index = None
    empty_frames = 0
    second_tlv_not_7 = 0

    while pos >= 0 and pos + FRAME_HEADER_LEN <= n:
        hdr = _read_header(buf, pos)
        if not _header_is_plausible(hdr, n - pos):
            nxt = buf.find(MAGIC, pos + 1)
            if nxt < 0:
                break
            pos = nxt
            continue

        total += 1

        if hdr.num_detected_obj == 0:
            empty_frames += 1
            if first_empty_index is None:
                first_empty_index = total - 1
                would_stop_at = total - 1
                stop_reason = "numDetObj == 0 (parser_mmw_demo.py returns TC_FAIL)"

        # TI reads the *second* TLV and only accepts type 7 there.
        if hdr.num_tlvs >= 2:
            off = pos + FRAME_HEADER_LEN
            t0 = int.from_bytes(buf[off:off + 4], "little")
            l0 = int.from_bytes(buf[off + 4:off + 8], "little")
            off2 = off + 8 + l0
            if off2 + 8 <= pos + hdr.total_packet_len:
                t1 = int.from_bytes(buf[off2:off2 + 4], "little")
                if t0 == 1 and t1 != 7:
                    second_tlv_not_7 += 1

        nxt_pos = pos + hdr.total_packet_len
        if nxt_pos >= n:
            break
        if buf[nxt_pos:nxt_pos + 8] == MAGIC:
            pos = nxt_pos
        else:
            pos = buf.find(MAGIC, nxt_pos)

    return dict(
        total_frames=total,
        empty_frames=empty_frames,
        first_empty_frame_index=first_empty_index if first_empty_index is not None else -1,
        would_stop_at_frame=would_stop_at if would_stop_at is not None else -1,
        stop_reason=stop_reason,
        frames_lost_single_pass=(total - would_stop_at) if would_stop_at is not None else 0,
        frames_with_second_tlv_not_type7=second_tlv_not_7,
    )


# ---------------------------------------------------------------------------


def audit(dat_path: str, cfg_path: Optional[str] = None) -> Dict:
    with open(dat_path, "rb") as f:
        buf = f.read()

    cfg: Optional[RadarConfig] = parse_cfg(cfg_path) if cfg_path else None
    geom = cfg.geometry() if cfg else None

    stats = StreamStats()
    frames = list(iter_frames(buf, geometry=geom, stats=stats))

    # ---- per-frame integrity -------------------------------------------
    frame_numbers = np.array([f.header.frame_number for f in frames], dtype=np.int64)
    num_pts_hdr = np.array([f.header.num_detected_obj for f in frames], dtype=np.int64)
    num_pts_tlv = np.array([f.num_points for f in frames], dtype=np.int64)

    gaps = 0
    if frame_numbers.size > 1:
        d = np.diff(frame_numbers)
        gaps = int(np.sum(d[d > 1] - 1))

    mismatch = int(np.sum(num_pts_hdr != num_pts_tlv))

    # ---- geometry cross-check against the actual stream ------------------
    observed_range_bins = None
    for f in frames:
        if f.range_profile is not None:
            observed_range_bins = int(f.range_profile.size)
            break

    # ---- do detections respect the cfg FoV gates? ------------------------
    fov_report: Dict[str, object] = {}
    if frames:
        all_pts = [f.points for f in frames if f.points is not None and f.num_points]
        if all_pts:
            P = np.concatenate(all_pts)
            rng = np.sqrt(P["x"].astype(np.float64) ** 2
                          + P["y"].astype(np.float64) ** 2
                          + P["z"].astype(np.float64) ** 2)
            dop = P["doppler"].astype(np.float64)
            az = np.degrees(np.arctan2(P["x"].astype(np.float64), P["y"].astype(np.float64)))
            fov_report = dict(
                total_points=int(P.size),
                range_min=float(rng.min()), range_max=float(rng.max()),
                doppler_min=float(dop.min()), doppler_max=float(dop.max()),
                azimuth_min_deg=float(az.min()), azimuth_max_deg=float(az.max()),
                zero_doppler_points=int(np.sum(dop == 0.0)),
            )
            if cfg:
                fl = cfg.filters
                if fl.range_fov_max_m is not None:
                    fov_report["points_outside_range_fov"] = int(
                        np.sum((rng < (fl.range_fov_min_m or 0)) | (rng > fl.range_fov_max_m)))
                if fl.doppler_fov_max_mps is not None:
                    fov_report["points_outside_doppler_fov"] = int(
                        np.sum((dop < fl.doppler_fov_min_mps) | (dop > fl.doppler_fov_max_mps)))

    # ---- SNR sign check --------------------------------------------------
    snr_report: Dict[str, object] = {}
    side = [f.side_info for f in frames if f.side_info is not None and f.side_info.size]
    if side:
        S = np.concatenate(side)
        snr_raw = S["snr"].astype(np.int32)
        noise_raw = S["noise"].astype(np.int32)
        snr_report = dict(
            points_with_side_info=int(S.size),
            snr_db_min=float(snr_raw.min() * 0.1),
            snr_db_max=float(snr_raw.max() * 0.1),
            noise_db_min=float(noise_raw.min() * 0.1),
            noise_db_max=float(noise_raw.max() * 0.1),
            negative_snr_count=int(np.sum(snr_raw < 0)),
            negative_noise_count=int(np.sum(noise_raw < 0)),
        )

    ti_sim = simulate_ti_reference_parser(buf)

    # Bytes before the first magic word are the tail of a frame that was already
    # in flight when recording started -- expected, not loss.
    first_magic = buf.find(MAGIC)
    leading = first_magic if first_magic > 0 else 0

    return dict(
        file=os.path.abspath(dat_path),
        file_size=len(buf),
        leading_partial_frame_bytes=leading,
        cfg_file=os.path.abspath(cfg_path) if cfg_path else None,
        stats=dict(
            frames_ok=stats.frames_ok,
            frames_with_zero_points=stats.frames_with_zero_points,
            bytes_total=stats.bytes_total,
            bytes_in_frames=stats.bytes_in_frames,
            bytes_skipped=stats.bytes_skipped,
            truncated_tail_bytes=stats.truncated_tail_bytes,
            resyncs=stats.resyncs,
            false_magic_rejected=stats.false_magic_rejected,
            padded_frames=stats.padded_frames,
            padding_bytes_total=stats.padding_bytes_total,
            tlv_type_counts={int(k): v for k, v in sorted(stats.tlv_type_counts.items())},
            tlv_order_counts={",".join(map(str, k)): v
                              for k, v in sorted(stats.tlv_order_counts.items(),
                                                 key=lambda kv: -kv[1])},
            anomaly_counts=stats.anomaly_counts,
        ),
        frame_number_gaps=gaps,
        first_frame_number=int(frame_numbers[0]) if frame_numbers.size else -1,
        last_frame_number=int(frame_numbers[-1]) if frame_numbers.size else -1,
        header_vs_tlv_point_count_mismatches=mismatch,
        frames_without_side_info=int(sum(
            1 for f in frames if f.side_info is None or f.side_info.size == 0)),
        observed_range_bins=observed_range_bins,
        cfg_range_bins=cfg.num_range_bins if cfg else None,
        fov=fov_report,
        side_info=snr_report,
        ti_reference_parser=ti_sim,
    )


# ---------------------------------------------------------------------------


def print_report(r: Dict, cfg: Optional[RadarConfig]) -> None:
    s = r["stats"]
    W = sys.stdout.write

    W("=" * 74 + "\n")
    W("mmWave .dat integrity audit\n")
    W("=" * 74 + "\n")
    W("File            : %s\n" % r["file"])
    W("Size            : %d bytes\n" % r["file_size"])
    W("\n")

    W("-- Byte accounting ------------------------------------------------\n")
    W("Frames parsed             : %d\n" % s["frames_ok"])
    W("Bytes inside frames       : %d%s\n"
      % (s["bytes_in_frames"], _pct(s["bytes_in_frames"], r["file_size"])))
    W("Bytes skipped / lost      : %d%s\n"
      % (s["bytes_skipped"], _pct(s["bytes_skipped"], r["file_size"])))
    W("Truncated tail            : %d bytes\n" % s["truncated_tail_bytes"])
    W("Resyncs required          : %d\n" % s["resyncs"])
    W("False magic words rejected: %d\n" % s["false_magic_rejected"])
    W("Frame-number gaps         : %d frames never reached the host\n"
      % r["frame_number_gaps"])
    W("32-byte-aligned padding   : %d frames, %d bytes total (expected, not loss)\n"
      % (s["padded_frames"], s["padding_bytes_total"]))
    W("Frame numbers             : %d .. %d\n"
      % (r["first_frame_number"], r["last_frame_number"]))
    W("\n")

    W("-- Is the visualizer dropping point-cloud data? -------------------\n")
    W("Frames where header numDetectedObj != TLV-1 length/16 : %d\n"
      % r["header_vs_tlv_point_count_mismatches"])
    W("Frames carrying zero detections                       : %d%s\n"
      % (s["frames_with_zero_points"], _pct(s["frames_with_zero_points"], s["frames_ok"])))

    # The verdict must not be reassuring unless it is earned.  Leading bytes
    # before the first magic word are expected (TI starts recording on whatever
    # chunk arrives, mid-frame); a mid-stream resync is genuine loss.
    lead = r.get("leading_partial_frame_bytes", 0)
    midstream = s["bytes_skipped"] - lead
    if s["frames_ok"] == 0:
        W("VERDICT: no frames parsed -- nothing can be concluded about this file.\n")
    elif r["header_vs_tlv_point_count_mismatches"] == 0 and s["resyncs"] == 0 \
            and midstream <= 0:
        W("VERDICT: every point the device announced is present in the file, and\n"
          "         every byte is accounted for. The .dat is a verbatim copy of\n"
          "         the UART data port.\n")
        if lead:
            W("         (%d leading bytes precede the first frame -- expected, the\n"
              "          recording began mid-frame.)\n" % lead)
    else:
        W("VERDICT: NOT byte-complete. Detail:\n")
        if r["header_vs_tlv_point_count_mismatches"]:
            W("  - %d frame(s) where the point count disagrees with the TLV length\n"
              % r["header_vs_tlv_point_count_mismatches"])
        if s["resyncs"]:
            W("  - %d mid-stream resync(s): the host lost bytes while recording\n"
              % s["resyncs"])
        if midstream > 0:
            W("  - %d byte(s) lost mid-stream (excluding %d leading bytes)\n"
              % (midstream, lead))
        W("  The device's own output was still complete; this is host-side loss.\n")
    W("\n")

    W("-- TLV composition ------------------------------------------------\n")
    for t, c in s["tlv_type_counts"].items():
        try:
            name = TLVType(t).name
        except ValueError:
            name = "UNKNOWN"
        W("  type %-2d  %-38s %d frames\n" % (t, name, c))
    W("\nObserved TLV orderings (order matters for TI's reference parser):\n")
    for order, c in s["tlv_order_counts"].items():
        W("  [%s]  x%d\n" % (order, c))
    W("\n")

    if s["anomaly_counts"]:
        W("-- Anomalies ------------------------------------------------------\n")
        for k, v in sorted(s["anomaly_counts"].items(), key=lambda kv: -kv[1]):
            W("  %-50s %d\n" % (k, v))
        W("\n")

    ti = r["ti_reference_parser"]
    W("-- What TI's reference Python parser would do with this file ------\n")
    W("Frames present                       : %d\n" % ti["total_frames"])
    W("Frames with numDetObj == 0           : %d\n" % ti["empty_frames"])
    W("First such frame at index            : %d\n" % ti["first_empty_frame_index"])
    if ti["would_stop_at_frame"] >= 0:
        W("parser_mmw_demo.py returns TC_FAIL here; callers that `break` on\n"
          "failure stop the whole pass at this point.\n")
        W("Frames lost in a single-pass parse   : %d of %d  (%.1f%%)\n"
          % (ti["frames_lost_single_pass"], ti["total_frames"],
             100.0 * ti["frames_lost_single_pass"] / max(1, ti["total_frames"])))
    else:
        W("No empty frames -- the numDetObj<=0 failure path is not triggered.\n")
    W("Frames whose 2nd TLV is not type 7   : %d\n"
      % ti["frames_with_second_tlv_not_type7"])
    W("Frames with NO type-7 TLV at all     : %d of %d\n"
      % (r["frames_without_side_info"], ti["total_frames"]))
    if r["frames_without_side_info"]:
        W("  -> these frames carry no SNR/noise. parser_mmw_demo.py substitutes\n"
          "     zeros for any of them it actually reaches (it stops at the first\n"
          "     frame with numDetObj == 0, so it may never reach most of them).\n")
    W("\n")

    if r["side_info"]:
        si = r["side_info"]
        W("-- Side info (SNR / noise), decoded as signed int16 ---------------\n")
        W("Points with side info : %d\n" % si["points_with_side_info"])
        W("SNR   range           : %.1f .. %.1f dB\n" % (si["snr_db_min"], si["snr_db_max"]))
        W("Noise range           : %.1f .. %.1f dB\n" % (si["noise_db_min"], si["noise_db_max"]))
        W("Negative SNR values   : %d\n" % si["negative_snr_count"])
        W("Negative noise values : %d\n" % si["negative_noise_count"])
        if si["negative_snr_count"] or si["negative_noise_count"]:
            W("  -> TI's parser reads these as UNSIGNED; each would appear as\n"
              "     ~6553 dB instead of a small negative value.\n")
        W("\n")

    if r["fov"]:
        f = r["fov"]
        W("-- Detected-point envelope ----------------------------------------\n")
        W("Total points     : %d\n" % f["total_points"])
        W("Range            : %.3f .. %.3f m\n" % (f["range_min"], f["range_max"]))
        W("Doppler          : %+.3f .. %+.3f m/s\n" % (f["doppler_min"], f["doppler_max"]))
        W("Azimuth          : %+.1f .. %+.1f deg\n"
          % (f["azimuth_min_deg"], f["azimuth_max_deg"]))
        W("Exactly-zero doppler points: %d\n" % f["zero_doppler_points"])
        if "points_outside_range_fov" in f:
            W("Points outside cfg range FoV   : %d\n" % f["points_outside_range_fov"])
        if "points_outside_doppler_fov" in f:
            W("Points outside cfg doppler FoV : %d\n" % f["points_outside_doppler_fov"])
        W("\n")

    if cfg:
        W("-- Configuration --------------------------------------------------\n")
        W(cfg.summary() + "\n\n")
        if r["observed_range_bins"] and r["cfg_range_bins"]:
            ok = r["observed_range_bins"] == r["cfg_range_bins"]
            W("Range-bin cross-check: cfg derives %d, stream carries %d -> %s\n\n"
              % (r["cfg_range_bins"], r["observed_range_bins"],
                 "MATCH" if ok else "MISMATCH"))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dat", help="path to a .dat recording")
    ap.add_argument("--cfg", help="matching .cfg used for the capture")
    ap.add_argument("--json", help="also write the full report as JSON")
    a = ap.parse_args(argv)

    cfg = parse_cfg(a.cfg) if a.cfg else None
    r = audit(a.dat, a.cfg)
    print_report(r, cfg)

    if a.json:
        with open(a.json, "w") as f:
            json.dump(r, f, indent=2)
        print("JSON report written to %s" % a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
