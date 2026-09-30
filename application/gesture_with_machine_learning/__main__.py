"""Run the gesture application live, or replay it against a recording.

    # TI gesture firmware flashed, read its features + probabilities:
    python -m mmwave_suite.application.gesture_with_machine_learning \
        live --mode onchip --cli COM4 --data COM5

    # Same, but re-run our copy of the network and prove it matches:
    python -m mmwave_suite.application.gesture_with_machine_learning \
        live --mode onchip_features --seconds 30

    # Stock out-of-box firmware, host features from the point cloud:
    python -m mmwave_suite.application.gesture_with_machine_learning \
        live --mode points --cfg cfg/xwr68xx_AOP_gesture_points_30fps.cfg

    # Collect a labelled take for retraining:
    python -m mmwave_suite.application.gesture_with_machine_learning \
        record --label l2r --seconds 30 --countdown 3 \
        --cfg cfg/xwr68xx_AOP_gesture_points_30fps.cfg --data-out data/gestures

    # Replay anything you recorded:
    python -m mmwave_suite.application.gesture_with_machine_learning \
        replay runs/run_.../run_....dat --mode points
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import Optional, Sequence

from ..common.app import run_live, run_offline
from .dataset import FeatureRecorder
from .features import HostFeatureConfig
from .pipeline import MODES, GesturePipeline

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_DIR = os.path.join(HERE, "cfg")
DEFAULT_CFG = {
    "onchip": "xwr68xx_AOP_gesture_ti-onchip_28fps.cfg",
    "onchip_features": "xwr68xx_AOP_gesture_ti-onchip_28fps.cfg",
    "points": "xwr68xx_AOP_gesture_points_30fps.cfg",
    "heatmap": "xwr68xx_AOP_gesture_rdmap_8fps.cfg",
}


def _cfg_for(mode: str, given: Optional[str]) -> str:
    if given:
        return given
    return os.path.join(CFG_DIR, DEFAULT_CFG[mode])


def _sidecar_cfg(dat_path: str) -> Optional[str]:
    run_dir = os.path.dirname(os.path.abspath(dat_path))
    found = glob.glob(os.path.join(run_dir, "*.cfg"))
    return found[0] if found else None


def _build(a) -> GesturePipeline:
    host = HostFeatureConfig(range_min_m=a.range_min, range_max_m=a.range_max,
                             doppler_min_mps=a.doppler_min,
                             snr_min_db=a.snr_min, max_points=a.max_points)
    return GesturePipeline(mode=a.mode, model_path=a.model, host_cfg=host)


def _printer(quiet: bool):
    def on_result(frame, res):
        if res.fired:
            print(">>> %s   (frame %d)" % (res.fired.upper(), res.frame_number),
                  flush=True)
        elif not quiet and res.frame_number % 30 == 0:
            print("    %s" % res, flush=True)
    return on_result


def _add_common(ap):
    ap.add_argument("--mode", choices=MODES, default="onchip")
    ap.add_argument("--model", help="path to an .npz model (default: TI's)")
    ap.add_argument("--range-min", type=float, default=0.05)
    ap.add_argument("--range-max", type=float, default=0.40)
    ap.add_argument("--doppler-min", type=float, default=0.0,
                    help="ignore points slower than this (0.47 mirrors TI)")
    ap.add_argument("--snr-min", type=float, default=6.0)
    ap.add_argument("--max-points", type=int, default=10)
    ap.add_argument("--quiet", action="store_true")


def _add_live(ap):
    ap.add_argument("--cli")
    ap.add_argument("--data")
    ap.add_argument("--cfg")
    ap.add_argument("--out", default="./runs")
    ap.add_argument("--run-label", dest="run_label",
                    help="free text appended to the run directory name and "
                         "stored verbatim in meta.json")
    ap.add_argument("--seconds", type=float, default=0.0)
    ap.add_argument("--countdown", type=int, default=0)
    ap.add_argument("--send-config", dest="send_config",
                    action="store_true", default=None,
                    help="force sending the .cfg (default: off in onchip "
                         "modes, on otherwise)")
    ap.add_argument("--no-send-config", dest="send_config",
                    action="store_false")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gesture_with_machine_learning", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_live = sub.add_parser("live", help="classify from the sensor")
    _add_common(p_live)
    _add_live(p_live)

    p_rec = sub.add_parser("record", help="record labelled features")
    _add_common(p_rec)
    _add_live(p_rec)
    p_rec.add_argument("--label", dest="gesture_label", required=True,
                       help="gesture class for this take, e.g. l2r")
    p_rec.add_argument("--data-out", default="./data/gestures")
    p_rec.add_argument("--notes", default="")

    p_rep = sub.add_parser("replay", help="classify from a .dat recording")
    _add_common(p_rep)
    p_rep.add_argument("dat")
    p_rep.add_argument("--cfg")
    p_rep.add_argument("--limit", type=int, default=0)

    a = ap.parse_args(argv)
    pipe = _build(a)
    on_result = _printer(a.quiet)

    if a.cmd == "replay":
        cfg = a.cfg or _sidecar_cfg(a.dat)
        if cfg is None:
            print("no .cfg beside %s; pass --cfg (needed for TLV geometry)"
                  % a.dat, file=sys.stderr)
            return 2
        n = run_offline(pipe, a.dat, cfg, on_result=on_result, limit=a.limit)
        print("\nreplayed %d frames" % n)
        print(pipe.summary())
        return 0

    cfg = _cfg_for(a.mode, a.cfg)
    if not os.path.exists(cfg):
        print("config not found: %s" % cfg, file=sys.stderr)
        return 2
    send = a.send_config
    if send is None:
        # TI's gesture binary self-configures and has no chirp CLI; pushing a
        # profileCfg at it errors per line. Everything else needs the config.
        send = a.mode not in ("onchip", "onchip_features")

    app = pipe
    if a.cmd == "record":
        app = FeatureRecorder(pipe, label=a.gesture_label, notes=a.notes)

    cap = run_live(app, cfg=cfg, cli=a.cli, data=a.data, out=a.out,
                   label=a.run_label, seconds=a.seconds,
                   countdown=a.countdown,
                   send_config=send, on_result=on_result)
    print("\n" + cap.summary())
    print(pipe.summary())
    if a.cmd == "record":
        try:
            path = app.save(a.data_out)
            print("\nwrote %s" % path)
        except RuntimeError as e:
            print("\nnot saved: %s" % e, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
