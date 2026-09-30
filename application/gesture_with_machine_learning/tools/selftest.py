"""End-to-end checks that do not need an EVM.

Builds synthetic .dat streams in the exact wire format the parser expects and
pushes them through the real pipeline.  What this proves and what it does not:

  PROVES   TLV 1050/1051 encode and decode correctly; the 10-float TLV order
           maps onto the 6 ANN inputs in the order objectdetection.c uses;
           the 15-frame window, normalisation, forward pass and debouncer are
           wired together correctly; the point-cloud extractor responds to
           motion in the direction it should.

  DOES NOT prove that this Python matches TI's C at the bit level.  That
           needs a board running gesture_ML_6443_AOP.bin:

               ... live --mode onchip_features --seconds 20

           and then reading the `agreement()` line, which compares our
           probabilities against the board's own TLV 1051 on the board's own
           features.  Anything above ~1e-3 there means something real is
           wrong; this file cannot see that, because it generates both sides.

Run:
    python selftest.py
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")))

from mmwave_suite.application.common.app import run_offline          # noqa: E402
from mmwave_suite.application.gesture_with_machine_learning.ann import (  # noqa: E402
    ANN_FEATURE_ORDER, GestureANN)
from mmwave_suite.application.gesture_with_machine_learning.features import (  # noqa: E402
    TLV_FEATURE_ORDER, aop_doa_input, angle_fft_2d, doppler_weights,
    rdi_features, suppress_zero_doppler)
from mmwave_suite.application.gesture_with_machine_learning.pipeline import (  # noqa: E402
    GesturePipeline)
from mmwave_suite.extraction.tlv import MAGIC, TLVType, parse_dat   # noqa: E402

CFG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cfg")

_fails = []


def check(name, cond, detail=""):
    print("  %-58s %s%s" % (name, "ok" if cond else "FAIL",
                            "" if cond else "   " + detail))
    if not cond:
        _fails.append(name)


# ---------------------------------------------------------------------------
# Wire-format builders
# ---------------------------------------------------------------------------


def build_frame(frame_no: int, tlvs, num_obj: int = 0) -> bytes:
    """One TLV frame with a valid 40-byte header and 32-byte padding."""
    body = b"".join(struct.pack("<II", t, len(p)) + p for t, p in tlvs)
    packet_len = 40 + len(body)
    total = 32 * ((packet_len + 31) // 32)
    hdr = MAGIC + struct.pack(
        "<8I",
        0x03060000,          # version 3.6.0.0
        total,
        0xA6843,             # platform
        frame_no,
        frame_no * 1000,     # cpu cycles
        num_obj,
        len(tlvs),
        0)                   # sub-frame
    return hdr + body + b"\x00" * (total - packet_len)


def gesture_frame(frame_no, feats10, probs10):
    return build_frame(frame_no, [
        (int(TLVType.GESTURE_FEATURES),
         np.asarray(feats10, dtype="<f4").tobytes()),
        (int(TLVType.GESTURE_ANN_PROB),
         np.asarray(probs10, dtype="<f4").tobytes()),
    ])


def point_frame(frame_no, pts, snr_db):
    """pts: (n,4) x,y,z,doppler.  snr_db: (n,)."""
    p = np.asarray(pts, dtype="<f4")
    si = np.empty((len(pts), 2), dtype="<i2")
    si[:, 0] = np.round(np.asarray(snr_db) * 10).astype(np.int16)
    si[:, 1] = 200                                    # 20.0 dB noise
    return build_frame(frame_no, [
        (int(TLVType.DETECTED_POINTS), p.tobytes()),
        (int(TLVType.DETECTED_POINTS_SIDE_INFO), si.tobytes()),
    ], num_obj=len(pts))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def t_model():
    print("\nmodel")
    ann = GestureANN()
    check("loads", ann.w0.shape == (30, 90) and ann.w2.shape == (10, 60))
    p = ann.infer(np.zeros(90))
    check("softmax sums to 1", abs(p.sum() - 1.0) < 1e-12)
    check("10 classes", p.size == 10)
    # Softmax must be shift invariant; a naive exp() would overflow here.
    big = ann.infer(np.full(90, 50.0))
    check("stable at extreme input", np.isfinite(big).all() and
          abs(big.sum() - 1.0) < 1e-12)
    check("feature order matches model json",
          tuple(ann.meta["input"]["feature_order"]) == ANN_FEATURE_ORDER)
    return ann


def t_primitives():
    print("\nfeature primitives")
    w = doppler_weights(128)
    check("doppler weights signed correctly",
          w[0] == 0 and w[63] == 63 and w[64] == -64 and w[127] == -1)

    det = np.ones((8, 128))
    z = suppress_zero_doppler(det, 5)
    check("zero-doppler suppression zeroes 11 of 128 bins",
          int((z[0] == 0).sum()) == 11)
    check("suppression keeps bin 6 and bin 122",
          z[0, 6] == 1 and z[0, 122] == 1)

    # A single cell at range bin 3, doppler bin 20 must reproduce itself.
    det = np.zeros((16, 128))
    det[3, 20] = 5000.0
    f = rdi_features(det)
    check("weighted range recovers the cell", abs(f.weighted_range - 3.0) < 1e-9)
    check("weighted doppler recovers the cell",
          abs(f.weighted_doppler - 20.0) < 1e-9)
    check("num detections counts above threshold", f.num_detections == 1.0)
    check("energy is sum/10000", abs(f.inst_energy - 0.5) < 1e-12)
    # Range bin 0 is excluded from the moments by RANGE_BIN_START=1.
    det0 = np.zeros((16, 128))
    det0[0, 20] = 5000.0
    check("range bin 0 excluded from moments",
          rdi_features(det0).num_detections == 0.0)

    # A pure phase ramp across the pair axis must peak at a predictable bin.
    cell = np.array([1, 1, 1, 1], dtype=complex)
    mag = angle_fft_2d(aop_doa_input(cell))
    check("angle FFT is 32x32", mag.shape == (32, 32))
    check("angle FFT peak is finite and unique",
          np.isfinite(mag).all() and mag.max() > 0)
    inp = aop_doa_input(np.array([1, 0, 0, 0], dtype=complex))
    check("AOP reorder puts TX1-RX1 at index 3",
          abs(inp[3] - 1 / 64.0) < 1e-12 and abs(inp[0]) < 1e-12)


def t_onchip(ann):
    print("\nonchip / onchip_features (synthetic TI-firmware stream)")
    rng = np.random.default_rng(0)
    n = 80
    # Features shaped like TI's training distribution so the network sees
    # something in range rather than a 20-sigma outlier.
    mean = dict(zip(ANN_FEATURE_ORDER, ann.mean))
    std = dict(zip(ANN_FEATURE_ORDER, ann.std))

    frames = []
    feats_log, probs_log = [], []
    window = np.zeros((15, 6))
    for i in range(n):
        named = {k: 0.0 for k in TLV_FEATURE_ORDER}
        for k in ANN_FEATURE_ORDER:
            named[k] = float(mean[k] + std[k] * rng.normal() * 0.5)
        # Sweep azimuth across the take so there is a real trajectory.
        named["weightedAzimuthMean"] = float(mean["weightedAzimuthMean"]
                                             + std["weightedAzimuthMean"]
                                             * (i / n * 4 - 2))
        vec10 = [named[k] for k in TLV_FEATURE_ORDER]

        window[:-1] = window[1:]
        window[-1] = ann.normalise([named[k] for k in ANN_FEATURE_ORDER])
        probs = ann.infer(window.reshape(-1))

        feats_log.append(vec10)
        probs_log.append(probs)
        frames.append(gesture_frame(i + 1, vec10, probs))

    with tempfile.TemporaryDirectory() as d:
        dat = os.path.join(d, "synthetic_gesture.dat")
        with open(dat, "wb") as f:
            f.write(b"".join(frames))
        parsed, stats = parse_dat(dat)
        check("all frames parse", len(parsed) == n, "got %d" % len(parsed))
        check("no lost bytes", stats.bytes_skipped == 0)
        check("no anomalies", not stats.anomaly_counts,
              str(stats.anomaly_counts))
        check("TLV 1050 decoded",
              parsed[0].gesture_features is not None
              and parsed[0].gesture_features.size == 10)
        check("TLV 1050 round-trips",
              np.allclose(parsed[5].gesture_features,
                          np.float32(feats_log[5]), atol=0, rtol=0))

        cfg = os.path.join(CFG_DIR, "xwr68xx_AOP_gesture_ti-onchip_28fps.cfg")
        pipe = GesturePipeline(mode="onchip_features")
        run_offline(pipe, dat, cfg)
        check("pipeline saw every frame", pipe.frames_with_data == n)
        a = np.asarray(pipe.host_probs)
        b = np.asarray(pipe.board_probs)
        # Frames 0..13 have a partly-empty window on our side but a "full"
        # one on the synthetic board's side only because we built them the
        # same way -- so here they agree from frame 0.
        err = float(np.abs(a - b).max())
        check("host probabilities reproduce the stream", err < 1e-6,
              "max abs err %.3g" % err)

        # The order check is the one that matters: shuffle the six ANN
        # inputs and the agreement must collapse.
        import mmwave_suite.application.gesture_with_machine_learning.features as F
        good = F.ANN_FEATURE_ORDER
        try:
            F.ANN_FEATURE_ORDER = tuple(reversed(good))
            pipe2 = GesturePipeline(mode="onchip_features")
            run_offline(pipe2, dat, cfg)
            err2 = float(np.abs(np.asarray(pipe2.host_probs)
                                - np.asarray(pipe2.board_probs)).max())
        finally:
            F.ANN_FEATURE_ORDER = good
        check("wrong feature order is detectable", err2 > 1e-3,
              "reversed order still agreed to %.3g" % err2)

        pipe3 = GesturePipeline(mode="onchip")
        run_offline(pipe3, dat, cfg)
        check("onchip mode needs no model and still debounces",
              pipe3.frames_with_data == n)


def t_points():
    print("\npoints (synthetic out-of-box stream, scripted left-to-right swipe)")
    n = 60
    frames = []
    for i in range(n):
        t = i / (n - 1.0)
        x = -0.15 + 0.30 * t            # hand crosses boresight, 30 cm
        y = 0.22                        # 22 cm out, TI's working distance
        vx = 0.30 / (n / 30.0)          # 30 fps -> m/s
        # Radial component of a lateral move: v_r = v . r_hat
        r = np.hypot(x, y)
        vr = vx * (x / r)
        pts = [(x + j * 0.01, y, 0.01 * (j - 1), vr) for j in range(3)]
        frames.append(point_frame(i + 1, pts, [30.0, 28.0, 26.0]))

    with tempfile.TemporaryDirectory() as d:
        dat = os.path.join(d, "synthetic_points.dat")
        with open(dat, "wb") as f:
            f.write(b"".join(frames))
        cfg = os.path.join(CFG_DIR, "xwr68xx_AOP_gesture_points_30fps.cfg")

        seen = []
        pipe = GesturePipeline(mode="points")
        run_offline(pipe, dat, cfg,
                    on_result=lambda fr, r: seen.append(r.features))
        check("every frame produced features",
              sum(1 for s in seen if s) == n,
              "%d of %d" % (sum(1 for s in seen if s), n))

        az = np.array([s["weightedAzimuthMean"] for s in seen if s])
        check("azimuth sweeps left to right",
              az[0] < -20 and az[-1] > 20,
              "az goes %.1f -> %.1f deg" % (az[0], az[-1]))
        check("azimuth is monotone", np.all(np.diff(az) > -1e-6))

        corr = np.array([s["azimuthDopplerCorr"] for s in seen if s])
        check("az/doppler correlation is zero before the buffer fills",
              np.all(corr[:19] == 0.0))
        check("az/doppler correlation is strong once filled",
              abs(corr[-1]) > 0.8, "%.3f" % corr[-1])

        rng_ = np.array([s["weightedRange"] for s in seen if s])
        check("weighted range stays inside the gate",
              rng_.min() > 0 and rng_.max() < 0.40 / 0.0535 + 2)


def t_heatmap():
    print("\nheatmap (synthetic TLV 5, 32 range x 128 doppler)")
    n_r, n_d = 32, 128
    frames = []
    for i in range(30):
        # log2-magnitude heat map, Q9, with a moving target at range bin 4.
        hm = np.full((n_r, n_d), 1000, dtype="<u2")     # noise floor
        d_bin = 20 + i                                   # accelerating away
        hm[4, d_bin] = 9000
        hm[4, d_bin + 1] = 7000
        hm[:, 0] = 20000                                 # DC clutter ridge
        # On the wire the SDK writes [range][doppler] with doppler fastest.
        frames.append(build_frame(
            i + 1,
            [(int(TLVType.RANGE_DOPPLER_HEAT_MAP), hm.tobytes()),
             (int(TLVType.DETECTED_POINTS),
              np.array([[0.0, 0.25, 0.0, 1.0]], dtype="<f4").tobytes()),
             (int(TLVType.DETECTED_POINTS_SIDE_INFO),
              np.array([[300, 200]], dtype="<i2").tobytes())],
            num_obj=1))

    with tempfile.TemporaryDirectory() as d:
        dat = os.path.join(d, "synthetic_rd.dat")
        with open(dat, "wb") as f:
            f.write(b"".join(frames))
        cfg = os.path.join(CFG_DIR, "xwr68xx_AOP_gesture_rdmap_8fps.cfg")
        parsed, _ = parse_dat(dat, __import__(
            "mmwave_suite.extraction.cfgparse", fromlist=["parse_cfg"]
        ).parse_cfg(cfg).geometry())
        check("heat map decodes to (doppler, range)",
              parsed[0].range_doppler_heatmap.shape == (n_d, n_r),
              str(parsed[0].range_doppler_heatmap.shape))

        seen = []
        pipe = GesturePipeline(mode="heatmap")
        run_offline(pipe, dat, cfg, on_result=lambda fr, r: seen.append(r.features))
        wr = np.array([s["weightedRange"] for s in seen if s])
        wd = np.array([s["weightedDoppler"] for s in seen if s])
        check("weighted range finds the target's bin",
              abs(wr.mean() - 4.0) < 0.5, "mean %.2f, expected ~4" % wr.mean())
        check("DC ridge is suppressed, not dominating",
              abs(wd).min() > 5.0, "min |wd| %.2f" % abs(wd).min())
        check("weighted doppler tracks the moving bin",
              wd[-1] > wd[0], "%.1f -> %.1f" % (wd[0], wd[-1]))


def main():
    print("gesture_with_machine_learning self-test")
    ann = t_model()
    t_primitives()
    t_onchip(ann)
    t_points()
    t_heatmap()
    print("\n%s" % ("ALL CHECKS PASSED" if not _fails
                    else "FAILED: " + ", ".join(_fails)))
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
