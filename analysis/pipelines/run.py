"""
pipelines/run.py -- the single entry point.

    # baseline: no filtering at all, straight to Plots/raw_unfiltered/
    python -m mmwave_suite.analysis.pipelines.run --dat RUN.dat --raw

    # one ghost method -> Plots/method4_mirror_geometry/
    python -m mmwave_suite.analysis.pipelines.run --dat RUN.dat --method method4_mirror_geometry

    # every method, plus the comparison figure
    python -m mmwave_suite.analysis.pipelines.run --dat RUN.dat --all-methods

    # micro-Doppler / gait only
    python -m mmwave_suite.analysis.pipelines.run --dat RUN.dat --gait

Output layout (created on demand):

    Plots/
      raw_unfiltered/<run>/       RT, VT, envelope, steps -- NO filtering
      <method_name>/<run>/        same figures after that method's filtering
      _comparison/<run>/          cross-method figures

Every run also writes `manifest.json` next to its figures: the parameters, the
fidelity tier, the integrity counters and the metrics. A figure without a
manifest is not reproducible, and a thesis figure that is not reproducible is a
liability.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

import numpy as np

from ..core.clutter import notch_zero_doppler, subtract_map_baseline
from ..core.io import load_recording
from ..core.maps import (range_time_map, velocity_time_map,
                        velocity_time_following, sparse_range_time_masked)
from ..core.recording import Recording
from ..ghost_sections import detectors as det
from ..ghost_sections.features import compute_features
from ..micro_doppler.envelope import envelope_from_points, foot_velocity_envelope
from ..micro_doppler.gait import cadence_from_spectrum, compute_gait_metrics
from ..micro_doppler.steps import detect_steps
from ..micro_doppler.tracking import track_from_points
from ..plotting import gait as gplot
from ..plotting import ghosts as ghplot
from ..plotting import maps as mplot
from ..plotting import pointcloud as pcplot
from ..plotting import diagnostics as dgplot

RAW_DIR = "raw_unfiltered"

# When plots live beside the recording, the run directory already identifies the
# run, so nesting a <run>/ folder under each method would just repeat it. When
# an explicit --plots-root aggregates several runs, that nesting is required to
# stop them colliding. `nest_by_run` carries that distinction.
_NEST_BY_RUN = True


def resolve_plots_root(dat_path: str, explicit: Optional[str]):
    """Where figures go.

    Default: `<the run's own directory>/Plots`. Keeping figures beside the .dat
    and .cfg that produced them means a run is self-describing -- you can move
    or archive one folder and the evidence travels with the data.

    Passing --plots-root overrides this to aggregate many runs in one tree.
    """
    if explicit:
        return os.path.abspath(explicit), True
    return os.path.join(os.path.dirname(os.path.abspath(dat_path)), "Plots"), False


def find_latest_dat(search_root: str = "runs") -> str:
    """Most recently modified .dat beneath `search_root`.

    Convenience for the common loop of record-then-analyse. Prints what it
    picked -- silently guessing which recording you meant is how you end up
    analysing yesterday's capture and not noticing.
    """
    if not os.path.isdir(search_root):
        raise FileNotFoundError(
            "No such directory: %s\nPass --dat explicitly, or --runs-root to "
            "point somewhere else." % os.path.abspath(search_root))
    cands = []
    for dp, _dn, fns in os.walk(search_root):
        for fn in fns:
            if fn.lower().endswith(".dat"):
                p = os.path.join(dp, fn)
                try:
                    cands.append((os.path.getmtime(p), p))
                except OSError:
                    pass
    if not cands:
        raise FileNotFoundError(
            "No .dat files found under %s" % os.path.abspath(search_root))
    cands.sort()
    latest = cands[-1][1]
    import datetime
    print("--latest resolved to: %s\n            modified: %s   (%d candidate%s)"
          % (latest,
             datetime.datetime.fromtimestamp(cands[-1][0]).strftime("%Y-%m-%d %H:%M:%S"),
             len(cands), "" if len(cands) == 1 else "s"))
    return latest


def _out_dir(root: str, bucket: str, run: str, nest_by_run: bool = True) -> str:
    d = os.path.join(root, bucket, run) if nest_by_run else os.path.join(root, bucket)
    os.makedirs(d, exist_ok=True)
    return d


def _json_safe(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (list, tuple)):
        return [_json_safe(x) for x in o]
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    return o


# ---------------------------------------------------------------------------


def run_gait(rec: Recording, out_dir: str, roi_m=None,
             v_min: float = 0.20, keep_mask: Optional[np.ndarray] = None,
             tag: str = "") -> Dict:
    """RT/VT maps, foot-velocity envelope, step events, gait metrics.

    `keep_mask` is a per-point boolean from a ghost method; None means raw.
    """
    g = rec.geom
    figs: List[str] = []
    suffix = ("_" + tag) if tag else ""
    ttl = " (" + tag + ")" if tag else ""

    # --- dense RT (TLV 2). NOTE: a measured range spectrum, so a point-cloud
    # ghost mask CANNOT change it. Never labelled "filtered".
    rt = range_time_map(rec, roi_m=roi_m)
    figs.append(mplot.plot_rt(rt, os.path.join(out_dir, "RT_dense.png"),
                              title="RT (dense, TLV-2) -- %s" % rec.name))
    rt_bs = subtract_map_baseline(rt)
    figs.append(mplot.plot_rt(
        rt_bs, os.path.join(out_dir, "RT_dense_baseline_removed.png"),
        title="RT dense, per-range baseline removed -- %s" % rec.name))

    # --- sparse RT from points: this one DOES respond to the ghost mask
    rt_sparse = sparse_range_time_masked(rec, keep_mask)
    figs.append(mplot.plot_rt(
        rt_sparse, os.path.join(out_dir, "RT_points%s.png" % suffix),
        title="RT from detected points%s -- %s" % (ttl, rec.name)))

    # --- tracking, then the target-following VT (the reference method)
    track = None
    try:
        tracks = track_from_points(rec, n_targets=1, min_abs_v=0.10)
        track = tracks[0] if tracks else None
    except Exception as e:                                  # pragma: no cover
        print("   (tracking failed: %r)" % (e,))

    if track is not None:
        figs.append(mplot.plot_rt(
            rt, os.path.join(out_dir, "RT_dense_with_track.png"),
            title="RT with target track -- %s" % rec.name,
            overlays=[dict(t=track.t, y=track.range_m, style="w-",
                           label="tracked range", lw=1.6)]))
        vt = velocity_time_following(rec, track, half_width_m=0.25,
                                     keep_mask=keep_mask)
        vt_name = "VT_following%s.png" % suffix
        vt_title = "VT, target-following +-0.25 m%s -- %s" % (ttl, rec.name)
    else:
        vt = velocity_time_map(rec, roi_m=roi_m)
        vt_name = "VT_fixed_roi%s.png" % suffix
        vt_title = "VT, fixed ROI%s -- %s" % (ttl, rec.name)

    figs.append(mplot.plot_vt(vt, os.path.join(out_dir, vt_name), title=vt_title))

    # fixed-ROI VT kept alongside, so the difference is visible
    vt_fixed = velocity_time_map(rec, roi_m=roi_m)
    figs.append(mplot.plot_vt(
        vt_fixed, os.path.join(out_dir, "VT_fixed_roi_compare.png"),
        title="VT, fixed ROI (for comparison) -- %s" % rec.name))

    figs.append(mplot.plot_rt_vt_pair(
        rt, vt, title="%s%s" % (rec.name, ttl),
        save_path=os.path.join(out_dir, "RT_VT_pair%s.png" % suffix)))

    # --- envelope
    f = rec.flat()
    sel = (keep_mask if keep_mask is not None and keep_mask.size == f["t"].size
           else np.ones(f["t"].size, bool))

    if rec.rd_map is not None:
        env = foot_velocity_envelope(vt)
    else:
        env = envelope_from_points(rec.t, f["frame_idx"][sel],
                                   f["v"][sel], rec.num_frames)

    steps = detect_steps(env, v_min=v_min)
    metrics = compute_gait_metrics(steps, rec.duration_s)
    spec = cadence_from_spectrum(env, g.fps)

    figs.append(gplot.plot_envelope(
        env, steps, metrics, v_min=v_min,
        title="Foot-velocity envelope -- %s%s" % (rec.name, " (" + tag + ")" if tag else ""),
        save_path=os.path.join(out_dir, "envelope_steps%s.png" % suffix)))
    figs.append(gplot.plot_step_intervals(
        steps, title="Step intervals -- %s" % rec.name,
        save_path=os.path.join(out_dir, "step_intervals%s.png" % suffix)))

    # --- point-cloud views (ported from dat_parser_plots_v2). Kept in their own
    # subfolder: they answer a different question from the dense maps above --
    # per-DETECTION attributes (SNR, azimuth, elevation) rather than per-CELL
    # signal. The angle panels in particular have no dense equivalent.
    pc_dir = os.path.join(out_dir, "point_cloud_views")
    os.makedirs(pc_dir, exist_ok=True)
    pc = pcplot.plot_all(rec, pc_dir, keep_mask=keep_mask, tag=tag)
    figs.extend(pc)

    # --- payload diagnostics: noise profile (TLV 3), DSP stats (TLV 6),
    # temperature (TLV 9), beamformed range-azimuth (TLV 4/8). These describe
    # the CAPTURE rather than the scene, so they are identical across ghost
    # methods and only written for the raw baseline.
    if keep_mask is None:
        dg_dir = os.path.join(out_dir, "payload_diagnostics")
        figs.extend(dgplot.plot_all(rec, dg_dir))

        # dense azimuth-time, if this capture has angle I/Q
        if rec.angle_iq is not None:
            try:
                from ..core.angle import azimuth_time_map
                at = azimuth_time_map(rec, track=track, half_width_m=0.5)
                figs.append(mplot.plot_time_map(
                    at, "Azimuth-Time (dense, TLV %d) -- %s"
                    % (rec.angle_tlv or 8, rec.name),
                    os.path.join(out_dir, "AT_dense_following.png")))
            except Exception as e:
                print("   (azimuth-time map failed: %r)" % (e,))

    return dict(
        figures=[os.path.basename(x) for x in figs if isinstance(x, str)],
        metrics=metrics.to_dict(),
        cadence_spectral=spec,
        cadence_agreement_pct=(
            abs(metrics.cadence_steps_per_min - spec["cadence_steps_per_min"])
            / max(metrics.cadence_steps_per_min, 1e-9) * 100
            if np.isfinite(spec["cadence_steps_per_min"]) else None),
        rt_provenance=rt.provenance,
        rt_sparse_provenance=rt_sparse.provenance,
        vt_provenance=vt.provenance,
        envelope_provenance=env.provenance,
        steps_provenance=steps.provenance,
    )


def run_raw(rec: Recording, plots_root: str, roi_m=None, v_min=0.20,
            nest_by_run: bool = True) -> Dict:
    """The unfiltered baseline. Bypasses every host-side filter by construction."""
    out = _out_dir(plots_root, RAW_DIR, rec.name, nest_by_run)
    print("[raw_unfiltered] -> %s" % out)

    res = run_gait(rec, out, roi_m=roi_m, v_min=v_min)

    pf = compute_features(rec)
    none = det.get("method0_none")(pf, rec.geom)
    ghplot.plot_range_time_tagged(
        pf, none, title="Range-Time, raw unfiltered -- %s" % rec.name,
        save_path=os.path.join(out, "range_time_raw.png"))
    ghplot.plot_feature_space(
        pf, none, save_path=os.path.join(out, "feature_space_raw.png"))

    manifest = dict(
        run=rec.name, bucket=RAW_DIR, source=rec.path,
        tier=rec.tier,
        geometry=dict(
            num_range_bins=rec.geom.num_range_bins,
            num_doppler_bins=rec.geom.num_doppler_bins,
            range_resolution_m=rec.geom.range_resolution_m,
            doppler_resolution_mps=rec.geom.doppler_resolution_mps,
            true_velocity_resolution_mps=rec.geom.true_velocity_resolution_mps,
            max_velocity_mps=rec.geom.max_velocity_mps,
            fps=rec.geom.fps),
        integrity=dict(lost_bytes=rec.lost_bytes, resyncs=rec.resyncs,
                       anomalies=rec.anomalies),
        on_chip_gating=rec.gating.__dict__,
        n_points=len(pf), n_frames=rec.num_frames,
        **res)
    with open(os.path.join(out, "manifest.json"), "w") as fh:
        json.dump(_json_safe(manifest), fh, indent=2)
    return manifest


def run_method(rec: Recording, method: str, plots_root: str,
               roi_m=None, v_min=0.20, nest_by_run: bool = True, **kw) -> Dict:
    out = _out_dir(plots_root, method, rec.name, nest_by_run)
    print("[%s] -> %s" % (method, out))

    pf = compute_features(rec)
    labels = det.get(method)(pf, rec.geom, **kw)
    print("   " + labels.report())

    ghplot.plot_before_after(
        pf, labels, save_path=os.path.join(out, "before_after.png"),
        range_max=rec.geom.num_range_bins * rec.geom.range_idx_to_meters)
    ghplot.plot_range_time_tagged(
        pf, labels, save_path=os.path.join(out, "range_time_tagged.png"))
    ghplot.plot_feature_space(
        pf, labels, save_path=os.path.join(out, "feature_space.png"))

    res = run_gait(rec, out, roi_m=roi_m, v_min=v_min,
                   keep_mask=~labels.is_ghost, tag="filtered")

    manifest = dict(
        run=rec.name, bucket=method, source=rec.path, tier=rec.tier,
        method=dict(name=labels.name, description=labels.description,
                    rationale=labels.rationale, params=labels.params,
                    n_tagged=labels.n_ghosts, fraction=labels.fraction),
        n_points=len(pf), n_frames=rec.num_frames,
        integrity=dict(lost_bytes=rec.lost_bytes, resyncs=rec.resyncs),
        **res)
    with open(os.path.join(out, "manifest.json"), "w") as fh:
        json.dump(_json_safe(manifest), fh, indent=2)
    return manifest


def run_all_methods(rec: Recording, plots_root: str, roi_m=None,
                    v_min=0.20, nest_by_run: bool = True) -> Dict:
    pf = compute_features(rec)
    labels = {}
    out = {}
    for m in det.available():
        if m == "method0_none":
            continue
        out[m] = run_method(rec, m, plots_root, roi_m=roi_m, v_min=v_min,
                            nest_by_run=nest_by_run)
        labels[m] = det.get(m)(pf, rec.geom)

    cmp_dir = _out_dir(plots_root, "_comparison", rec.name, nest_by_run)
    ghplot.plot_method_comparison(
        pf, labels, save_path=os.path.join(cmp_dir, "method_comparison.png"))
    txt = det.agreement_matrix(pf, rec.geom)
    with open(os.path.join(cmp_dir, "agreement.txt"), "w") as fh:
        fh.write(txt + "\n")
    print("\n" + txt)
    return out


# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dat", help="path to a .dat recording")
    ap.add_argument("--latest", action="store_true",
                    help="use the most recent .dat under --runs-root")
    ap.add_argument("--runs-root", default="runs",
                    help="where --latest searches (default: runs)")
    ap.add_argument("--cfg", help="matching .cfg (default: the one beside it)")
    ap.add_argument("--plots-root",
                    help="aggregate figures here instead of beside the "
                         "recording (default: <run dir>/Plots)")
    ap.add_argument("--raw", action="store_true",
                    help="unfiltered baseline -> Plots/raw_unfiltered/")
    ap.add_argument("--method", help="one ghost method by name")
    ap.add_argument("--all-methods", action="store_true")
    ap.add_argument("--gait", action="store_true",
                    help="micro-Doppler / gait only, no ghost tagging")
    ap.add_argument("--roi", nargs=2, type=float, metavar=("MIN_M", "MAX_M"),
                    help="range ROI in metres (paper section 2.3 gating)")
    ap.add_argument("--v-min", type=float, default=0.20,
                    help="minimum step peak height, m/s (paper: 0.20)")
    ap.add_argument("--list-methods", action="store_true")
    a = ap.parse_args(argv)

    if a.list_methods:
        print("Ghost methods:")
        for m in det.available():
            print("  %-26s %s" % (m, det.get(m).description))
        return 0

    if a.latest and not a.dat:
        a.dat = find_latest_dat(a.runs_root)
    if not a.dat:
        ap.error("give --dat PATH, or --latest to use the newest recording "
                 "under %r" % a.runs_root)

    rec = load_recording(a.dat, a.cfg)
    print(rec.summary())
    print()
    print("Payloads present in this recording:")
    for k, v in rec.available_payloads.items():
        print("  %-26s %s" % (k, "yes" if v else "-- absent"))
    print()

    if rec.resyncs:
        print("WARNING: %d mid-stream resync(s) -- this capture lost bytes on "
              "the host. Results remain valid for the frames present, but the "
              "time base has gaps.\n" % rec.resyncs)

    plots_root, nest = resolve_plots_root(a.dat, a.plots_root)
    print("Figures -> %s%s\n"
          % (plots_root, "" if nest else "   (beside the recording)"))

    roi = tuple(a.roi) if a.roi else None

    if not any([a.raw, a.method, a.all_methods, a.gait]):
        a.raw = True     # sensible default: establish the baseline first

    if a.raw:
        run_raw(rec, plots_root, roi_m=roi, v_min=a.v_min, nest_by_run=nest)
    if a.gait:
        out = _out_dir(plots_root, "gait_only", rec.name, nest)
        res = run_gait(rec, out, roi_m=roi, v_min=a.v_min)
        print(json.dumps(_json_safe(res["metrics"]), indent=2))
    if a.method:
        run_method(rec, a.method, plots_root, roi_m=roi, v_min=a.v_min,
                   nest_by_run=nest)
    if a.all_methods:
        run_all_methods(rec, plots_root, roi_m=roi, v_min=a.v_min,
                        nest_by_run=nest)

    print("\nDone. Figures under %s" % plots_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
