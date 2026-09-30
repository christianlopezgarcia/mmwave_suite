"""Record labelled feature streams, and cut them into training windows.

TI's own advice for retraining is one sentence in their users guide -- "save
the extracted features which are output over UART and use the saved features
as training data" -- and no tooling.  This is that tooling.

A session is one .npz per label:

    features   (n_frames, 10) float32   raw, UNNORMALISED, TLV order
    t          (n_frames,)    float64   seconds since the first frame
    frame      (n_frames,)    int32     device frame number
    label      ()             str
    meta       ()             json str  mode, cfg path, run dir, notes

Raw and unnormalised on purpose: normalisation constants are part of the
model, and you will want to recompute them over the whole corpus rather than
inherit TI's, which were fitted to their firmware's magnitude scale.

Labelling by session rather than per frame is the pragmatic choice -- you
cannot reliably mark the instant a swipe begins by hand.  `windows()` turns a
session into fixed-length windows and labels a window with the session label
only if it contains enough motion, so the idle frames between repetitions
become `no_gesture` examples instead of mislabelled noise.  That ratio is the
single biggest lever on a retrained model's false-positive rate.
"""

from __future__ import annotations

import glob
import json
import os
from typing import List, Optional, Sequence, Tuple

import numpy as np

from ..common.app import AppModule
from .ann import ANN_FEATURE_ORDER, CLASS_NAMES, FEATURE_LEN
from .features import TLV_FEATURE_ORDER


class FeatureRecorder(AppModule):
    """An AppModule that stores features instead of classifying them.

    Wrap a GesturePipeline to reuse its front end:

        pipe = GesturePipeline(mode="points")
        rec  = FeatureRecorder(pipe, label="l2r")
        run_live(rec, cfg=..., ...)
        rec.save("data/gestures")
    """

    name = "gesture_feature_recorder"

    def __init__(self, pipeline, label: str, notes: str = ""):
        self.pipeline = pipeline
        self.label = label
        self.notes = notes
        self.reset()

    def reset(self) -> None:
        self.pipeline.reset()
        self._feat: List[List[float]] = []
        self._t: List[float] = []
        self._frame: List[int] = []
        self._t0: Optional[float] = None

    def on_frame(self, frame):
        res = self.pipeline.on_frame(frame)
        if not res.features:
            return res
        ts = frame.host_timestamp
        if ts is None:
            ts = 0.0
        if self._t0 is None:
            self._t0 = ts
        self._feat.append([float(res.features.get(k, 0.0))
                           for k in TLV_FEATURE_ORDER])
        self._t.append(ts - self._t0)
        self._frame.append(int(frame.header.frame_number))
        return res

    def save(self, out_dir: str, name: Optional[str] = None) -> str:
        if not self._feat:
            raise RuntimeError("nothing recorded -- no frame produced features")
        os.makedirs(out_dir, exist_ok=True)
        base = name or ("%s_%d" % (self.label, len(self._feat)))
        path = os.path.join(out_dir, base + ".npz")
        i = 1
        while os.path.exists(path):        # never silently overwrite a take
            path = os.path.join(out_dir, "%s_%02d.npz" % (base, i))
            i += 1
        np.savez(
            path,
            features=np.asarray(self._feat, dtype=np.float32),
            t=np.asarray(self._t, dtype=np.float64),
            frame=np.asarray(self._frame, dtype=np.int32),
            label=np.array(self.label),
            meta=np.array(json.dumps({
                "mode": self.pipeline.mode,
                "notes": self.notes,
                "feature_order": list(TLV_FEATURE_ORDER),
                "n_frames": len(self._feat),
            })),
        )
        return path

    def summary(self) -> str:
        return "recorded %d frames labelled '%s'" % (len(self._feat), self.label)


# ---------------------------------------------------------------------------
# Corpus -> training tensors
# ---------------------------------------------------------------------------


def load_sessions(root: str) -> List[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(root, "**", "*.npz"), recursive=True)):
        d = np.load(p, allow_pickle=False)
        if "features" not in d or "label" not in d:
            continue
        out.append({
            "path": p,
            "features": d["features"].astype(np.float64),
            "label": str(d["label"]),
            "t": d["t"] if "t" in d else None,
        })
    return out


def _motion_score(win: np.ndarray, cols: Sequence[int]) -> float:
    """How much the window actually moves, in units of its own scale.

    Peak-to-peak of the angle and Doppler columns.  A window from the pause
    between two swipes scores near zero; one containing the swipe does not.
    """
    sub = win[:, list(cols)]
    rng = sub.max(axis=0) - sub.min(axis=0)
    return float(np.max(rng))


def windows(sessions: List[dict],
            feature_names: Sequence[str] = ANN_FEATURE_ORDER,
            length: int = FEATURE_LEN,
            stride: int = 1,
            motion_quantile: float = 0.6,
            class_names: Sequence[str] = CLASS_NAMES
            ) -> Tuple[np.ndarray, np.ndarray, dict]:
    """Cut sessions into (n, length*n_features) windows with integer labels.

    A window keeps its session's label only if its motion score is in the top
    `1 - motion_quantile` of that session; quieter windows become
    `no_gesture`.  With motion_quantile=0.6 roughly 40% of each take is
    positive, which for a 9-gesture set lands near a 1:1 gesture-to-idle
    balance overall.  Raise it if the model fires on nothing.
    """
    idx = {n: i for i, n in enumerate(TLV_FEATURE_ORDER)}
    cols = [idx[n] for n in feature_names]
    # Columns whose movement indicates a gesture is happening.
    motion_cols = [c for c, n in zip(cols, feature_names)
                   if n in ("weightedAzimuthMean", "weightedElevationMean",
                            "weightedDoppler")]
    name_to_i = {n: i for i, n in enumerate(class_names)}

    X: List[np.ndarray] = []
    y: List[int] = []
    for s in sessions:
        f = s["features"]
        if f.shape[0] < length:
            continue
        label_i = name_to_i.get(s["label"])
        if label_i is None:
            raise ValueError("session %s has unknown label %r (known: %s)"
                             % (s["path"], s["label"], list(class_names)))
        wins = [f[i:i + length] for i in range(0, f.shape[0] - length + 1, stride)]
        scores = np.array([_motion_score(w, motion_cols) for w in wins])
        cut = np.quantile(scores, motion_quantile) if scores.size else 0.0
        for w, sc in zip(wins, scores):
            X.append(w[:, cols].reshape(-1))
            y.append(label_i if (sc >= cut and label_i != 0) else 0)

    if not X:
        raise RuntimeError("no windows produced -- sessions shorter than %d frames?"
                           % length)
    Xa = np.asarray(X, dtype=np.float64)
    ya = np.asarray(y, dtype=np.int64)
    counts = {class_names[i]: int((ya == i).sum()) for i in np.unique(ya)}
    return Xa, ya, {"counts": counts, "n_features": len(feature_names),
                    "length": length, "feature_order": list(feature_names)}


def fit_normalisation(X: np.ndarray, n_features: int, length: int
                      ) -> Tuple[np.ndarray, np.ndarray]:
    """Per-feature mean/std across all frames of all windows.

    Fitted per FEATURE, not per input position, so that the same constants
    apply to a frame wherever it sits in the window -- which is what the
    runtime does (it normalises on entry, once).
    """
    f = X.reshape(X.shape[0], length, n_features)
    mean = f.reshape(-1, n_features).mean(axis=0)
    std = f.reshape(-1, n_features).std(axis=0)
    std[std < 1e-9] = 1.0
    return mean, std


def apply_normalisation(X: np.ndarray, mean: np.ndarray, std: np.ndarray,
                        n_features: int, length: int) -> np.ndarray:
    f = X.reshape(X.shape[0], length, n_features)
    return ((f - mean) / std).reshape(X.shape[0], -1)
