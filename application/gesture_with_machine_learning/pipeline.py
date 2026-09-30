"""Frame in, gesture out.  Three source modes, one interface.

    mode="onchip"   TI's gesture firmware is flashed.  The board has already
                    done the features AND the inference; we read TLV 1050 and
                    1051 and only re-run the debouncer on the host so the
                    thresholds are ours to tune.  Zero fidelity loss.

    mode="onchip_features"
                    TI's firmware is flashed, but we ignore its probabilities
                    and re-run our own copy of the network on its TLV-1050
                    features.  This is the mode that PROVES the port: the
                    probabilities must match the board's to ~1e-6.  Use it
                    once, to validate, then use it whenever you retrain on
                    TI-firmware features.

    mode="points"   Stock out-of-box firmware.  Features from TLV 1 + 7.
                    Full frame rate, tenth of the bandwidth, and REQUIRES a
                    retrained model -- see the note in features.py.

    mode="heatmap"  Stock out-of-box firmware with TLV 5 enabled.  RDI
                    features with TI's definitions, angle features from the
                    point cloud.  Closest in spirit to the board, but the
                    UART caps it near 6 fps at 64x128, which is too slow for
                    gestures.  Provided for offline study of recordings, not
                    for live use.

Everything downstream of the feature vector -- the 15-frame window, the
normalisation, the network, the debouncer -- is shared by all four, so a
comparison between modes is a comparison of the front end alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

import numpy as np

from ..common.app import AppModule
from .ann import ANN_FEATURE_ORDER, CLASS_NAMES, GestureANN
from .features import (TLV_FEATURE_ORDER, FeatureWindow, HeatmapFeatureExtractor,
                       HostFeatureConfig, PointCloudFeatureExtractor,
                       order_for_ann)
from .postproc import (GestureDebouncer, TI_COUNT_THRESHOLDS,
                       TI_VISUALIZER_PROB_THRESHOLDS)

MODES = ("onchip", "onchip_features", "points", "heatmap")


@dataclass
class GestureResult:
    frame_number: int = 0
    features: dict = field(default_factory=dict)
    probs: Optional[np.ndarray] = None
    state: str = "no_gesture"
    fired: Optional[str] = None
    ready: bool = False

    def __str__(self) -> str:
        if self.probs is None:
            return "frame %d: no data" % self.frame_number
        i = int(np.argmax(self.probs))
        return ("frame %-7d %-12s p=%.2f  state=%s%s"
                % (self.frame_number, CLASS_NAMES[i], self.probs[i], self.state,
                   "  <<< %s" % self.fired if self.fired else ""))


class GesturePipeline(AppModule):
    name = "gesture_with_machine_learning"

    def __init__(self,
                 mode: str = "onchip",
                 model_path: Optional[str] = None,
                 host_cfg: Optional[HostFeatureConfig] = None,
                 prob_thresholds=TI_VISUALIZER_PROB_THRESHOLDS,
                 count_thresholds=TI_COUNT_THRESHOLDS,
                 log2lin_scale: float = 1.0 / 512.0,
                 wait_for_full_window: bool = True):
        if mode not in MODES:
            raise ValueError("mode must be one of %s" % (MODES,))
        self.mode = mode
        self.wait_for_full_window = wait_for_full_window

        # "onchip" never runs the network, so a missing model file must not
        # stop you reading a board that is already doing the work.
        self.ann = None
        if mode != "onchip":
            self.ann = GestureANN(model_path)
        self.window = FeatureWindow(self.ann) if self.ann else None

        self.host_cfg = host_cfg or HostFeatureConfig()
        self.points = PointCloudFeatureExtractor(self.host_cfg)
        self.heatmap = HeatmapFeatureExtractor(self.host_cfg,
                                               log2lin_scale=log2lin_scale)
        self.debouncer = GestureDebouncer(prob_thresholds, count_thresholds)
        self.reset()

    # -- AppModule ----------------------------------------------------------

    def reset(self) -> None:
        if self.window is not None:
            self.window.reset()
        self.debouncer.reset()
        self.frames = 0
        self.frames_with_data = 0
        self.history: List[dict] = []
        self.board_probs: List[np.ndarray] = []
        self.host_probs: List[np.ndarray] = []

    def on_frame(self, frame) -> GestureResult:
        self.frames += 1
        res = GestureResult(frame_number=frame.header.frame_number)

        named = self._features(frame)
        if named is None:
            return res
        self.frames_with_data += 1
        res.features = named

        probs = self._probabilities(frame, named)
        if probs is None:
            return res
        res.probs = probs
        res.ready = self.window is None or self.window.ready

        if res.ready or not self.wait_for_full_window:
            res.fired = self.debouncer.update(probs)
            res.state = self.debouncer.state
        return res

    # -- internals ----------------------------------------------------------

    def _features(self, frame) -> Optional[dict]:
        if self.mode in ("onchip", "onchip_features"):
            v = getattr(frame, "gesture_features", None)
            if v is None or len(v) != len(TLV_FEATURE_ORDER):
                return None
            return dict(zip(TLV_FEATURE_ORDER, (float(x) for x in v)))

        if self.mode == "points":
            named = self.points(frame)
        else:
            named = self.heatmap(frame, self.host_cfg.range_bin_m)
            # TLV 5 carries no phase, so the two angle columns come from the
            # point cloud even in heatmap mode.
            pc = self.points(frame)
            for k in ("weightedAzimuthMean", "weightedElevationMean",
                      "weightedAzimuthDispersion", "weightedElevationDispersion"):
                named[k] = pc[k]

        named["azimuthDopplerCorr"] = self.window.correlation(
            named["weightedAzimuthMean"], named["weightedDoppler"])
        return named

    def _probabilities(self, frame, named: dict) -> Optional[np.ndarray]:
        board = getattr(frame, "gesture_probs", None)
        if self.mode == "onchip":
            return None if board is None else np.asarray(board, dtype=np.float64)

        vec = self.window.push(order_for_ann(named))
        probs = self.ann.infer(vec)
        if board is not None:
            # Only populated in onchip_features mode, where it is the whole
            # point: keep both so `agreement()` can quantify the port.
            self.board_probs.append(np.asarray(board, dtype=np.float64))
            self.host_probs.append(probs)
        return probs

    # -- reporting ----------------------------------------------------------

    def agreement(self) -> Optional[str]:
        """Max/mean |host - board| over the probability vectors.

        Only meaningful in `onchip_features` mode.  Expect ~1e-6 (float32 on
        the R4F against float64 here).  Anything above ~1e-3 means the
        feature ORDER is wrong, which is the failure this exists to catch --
        see ANN_FEATURE_ORDER.
        """
        if not self.board_probs:
            return None
        a = np.asarray(self.host_probs)
        b = np.asarray(self.board_probs)
        n = min(len(a), len(b))
        # The board's probabilities for frame k reflect ITS window ending at
        # k; ours only match once our window has also filled.
        d = np.abs(a[:n] - b[:n])
        warm = d[15:] if n > 15 else d
        return ("host vs board over %d frames: max %.3g, mean %.3g "
                "(first 15 frames excluded: window warm-up)"
                % (n, float(warm.max()) if warm.size else float("nan"),
                   float(warm.mean()) if warm.size else float("nan")))

    def summary(self) -> str:
        lines = ["mode=%s  frames=%d  frames_with_features=%d"
                 % (self.mode, self.frames, self.frames_with_data)]
        if self.frames and not self.frames_with_data:
            lines.append(
                "  NOTHING DECODED. In onchip mode that means the EVM is not "
                "running TI's gesture binary (no TLV 1050/1051). In points "
                "mode it means no point passed the range/SNR gate -- check "
                "HostFeatureConfig.range_max_m against where your hand was.")
        lines.append("  " + self.debouncer.summary())
        ag = self.agreement()
        if ag:
            lines.append("  " + ag)
        return "\n".join(lines)
