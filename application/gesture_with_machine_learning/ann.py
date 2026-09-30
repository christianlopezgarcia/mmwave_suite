"""The gesture classifier: a 90-30-60-10 MLP, in numpy.

This is a line-for-line port of `include/utils/ann_utils.c` from TI's lab.
It is deliberately not a framework model -- there is no training graph, no
optimiser state, nothing to version-skew.  Eight arrays and three matmuls.

Layer sizes come from gesture.h:68-78:

    input   90 = 6 features x 15 frames
    dense1  30  + ReLU
    dense2  60  + ReLU
    dense3  10  + softmax          -> 10 class probabilities

`GestureANN.infer` reproduces `annUtils_inference` exactly, including the
max-subtracted softmax.  That matters more than it sounds: if you ever want to
check a host-side feature extractor against the on-chip one, you compare
probabilities, and a different softmax would hide a real mismatch behind a
plausible-looking difference.
"""

from __future__ import annotations

import json
import os
from typing import Optional, Sequence

import numpy as np

# gesture.h:68-78
NUM_FEATURES = 6
FEATURE_LEN = 15
INP_DIM = NUM_FEATURES * FEATURE_LEN

# gesture.h:91-100 -- index order of the softmax output.
CLASS_NAMES = (
    "no_gesture",
    "l2r",        # swipe left to right
    "r2l",        # swipe right to left
    "u2d",        # swipe up to down
    "d2u",        # swipe down to up
    "twirl_cw",   # clockwise finger twirl   ("volume up")
    "twirl_ccw",  # anticlockwise twirl      ("volume down")
    "off_push",   # hand moves toward the radar
    "on_pull",    # hand moves away
    "shine",
)

# objectdetection.c:954-959 -- the six features, in the order the C code writes
# them into the sliding window.  NOT the order they appear in Features_t, and
# NOT the order of the 10-float TLV.  Getting this wrong silently produces a
# model that is 20% accurate, so it is asserted against the .json on load.
ANN_FEATURE_ORDER = (
    "weightedDoppler",
    "weightedAzimuthMean",
    "weightedElevationMean",
    "numDetections",
    "weightedRange",
    "azimuthDopplerCorr",
)

_DEFAULT_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "model", "ti_ann_6843.npz")


class GestureANN:
    """Load once, call `infer` per frame."""

    def __init__(self, npz_path: Optional[str] = None):
        self.path = npz_path or _DEFAULT_MODEL
        if not os.path.exists(self.path):
            raise FileNotFoundError(
                "%s not found. Regenerate it with\n"
                "  python -m mmwave_suite.application."
                "gesture_with_machine_learning.tools.export_ti_weights"
                % self.path)
        d = np.load(self.path)
        self.mean = d["mean"].astype(np.float64)
        self.std = d["std"].astype(np.float64)
        self.w0 = d["w0"].astype(np.float64)
        self.w1 = d["w1"].astype(np.float64)
        self.w2 = d["w2"].astype(np.float64)
        self.b0 = d["b0"].astype(np.float64)
        self.b1 = d["b1"].astype(np.float64)
        self.b2 = d["b2"].astype(np.float64)

        if self.w0.shape != (self.b0.size, INP_DIM):
            raise ValueError("w0 is %s, expected (%d, %d)"
                             % (self.w0.shape, self.b0.size, INP_DIM))
        if self.mean.size != NUM_FEATURES or self.std.size != NUM_FEATURES:
            raise ValueError("mean/std must have %d entries" % NUM_FEATURES)
        if np.any(self.std == 0):
            raise ValueError("a std entry is zero; normalisation would divide by 0")

        meta_path = os.path.splitext(self.path)[0] + ".json"
        self.meta = {}
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                self.meta = json.load(f)
            order = tuple(self.meta.get("input", {}).get("feature_order", ()))
            if order and order != ANN_FEATURE_ORDER:
                raise ValueError(
                    "model feature order %s does not match ANN_FEATURE_ORDER %s"
                    % (order, ANN_FEATURE_ORDER))
        self.class_names = tuple(self.meta.get("classes", CLASS_NAMES))

    # -- normalisation ------------------------------------------------------

    def normalise(self, feats: Sequence[float]) -> np.ndarray:
        """(x - mean)/std for one frame's six features.

        objectdetection.c normalises each frame as it enters the window, not
        the window as a whole, so a retrained model must do the same or the
        oldest frames end up scaled by the wrong statistics.
        """
        x = np.asarray(feats, dtype=np.float64)
        if x.size != NUM_FEATURES:
            raise ValueError("expected %d features, got %d" % (NUM_FEATURES, x.size))
        return (x - self.mean) / self.std

    # -- forward pass -------------------------------------------------------

    def infer(self, window: np.ndarray) -> np.ndarray:
        """window: 90 already-normalised floats, oldest frame first.

        Returns 10 probabilities.  ann_utils.c:119-156.
        """
        x = np.asarray(window, dtype=np.float64).reshape(-1)
        if x.size != INP_DIM:
            raise ValueError("expected %d inputs, got %d" % (INP_DIM, x.size))
        h1 = np.maximum(self.w0 @ x + self.b0, 0.0)     # dense + ReLU
        h2 = np.maximum(self.w1 @ h1 + self.b1, 0.0)
        z = self.w2 @ h2 + self.b2
        z = z - z.max()                                  # stable softmax
        e = np.exp(z)
        return e / e.sum()

    def top(self, probs: np.ndarray):
        i = int(np.argmax(probs))
        return self.class_names[i], float(probs[i]), i

    def __repr__(self) -> str:
        return ("GestureANN(%d -> %d -> %d -> %d, %s)"
                % (INP_DIM, self.b0.size, self.b1.size, self.b2.size,
                   os.path.basename(self.path)))
