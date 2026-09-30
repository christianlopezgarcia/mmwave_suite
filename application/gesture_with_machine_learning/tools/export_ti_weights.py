"""Extract TI's trained gesture ANN from the C headers into a .npz.

The on-chip model lives as plain comma-separated float literals inside
``src/6443/include/neuralnet/{mean,std,b0,b1,b2,w0,w1,w2}.h``.  ``ann_params.h``
#includes them, in that order, as the brace-initialiser of ``ANN_struct_t``
(gesture.h:197), so the literal order in each file IS the C array order:
``W_0`` is declared ``float W_0[30][90]`` and therefore stores row-major with
the 90-long input dimension contiguous.

Run:
    python -m mmwave_suite.application.gesture_with_machine_learning.tools.export_ti_weights

Writes ``model/ti_ann_6843.npz`` and ``model/ti_ann_6843.json`` next to this
package.  Re-run it after replacing the headers with a retrained model.
"""

from __future__ import annotations

import json
import os
import re
import sys

import numpy as np

# gesture.h:68-78
NUM_FEATURES = 6
FEATURE_LEN = 15
INP_DIM = NUM_FEATURES * FEATURE_LEN   # 90
L1, L2, L3 = 30, 60, 10

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
DEFAULT_HEADERS = os.path.join(PKG, "ti_reference", "src", "6443", "include", "neuralnet")
DEFAULT_OUT = os.path.join(PKG, "model")

_FLOAT = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?[fF]?")


def _load(path: str) -> np.ndarray:
    """Every float literal in a header, in file order, minus the licence text.

    The licence block contains no bare numerals that survive the comment strip,
    but strip comments anyway rather than rely on that -- a future header with
    a version number in a comment would otherwise silently shift every weight.
    """
    with open(path, "r", errors="replace") as f:
        text = f.read()
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)   # block comments
    text = re.sub(r"//[^\n]*", " ", text)                # line comments
    text = re.sub(r"^\s*#.*$", " ", text, flags=re.M)    # preprocessor
    return np.array([float(m.group(0).rstrip("fF")) for m in _FLOAT.finditer(text)],
                    dtype=np.float32)


def export(headers_dir: str = DEFAULT_HEADERS, out_dir: str = DEFAULT_OUT) -> str:
    want = {
        "mean": (NUM_FEATURES,),
        "std": (NUM_FEATURES,),
        "b0": (L1,),
        "b1": (L2,),
        "b2": (L3,),
        "w0": (L1, INP_DIM),
        "w1": (L2, L1),
        "w2": (L3, L2),
    }
    arrays = {}
    for name, shape in want.items():
        path = os.path.join(headers_dir, name + ".h")
        vals = _load(path)
        n = int(np.prod(shape))
        if vals.size != n:
            raise ValueError("%s: expected %d floats for shape %s, found %d"
                             % (path, n, shape, vals.size))
        arrays[name] = vals.reshape(shape)

    os.makedirs(out_dir, exist_ok=True)
    npz = os.path.join(out_dir, "ti_ann_6843.npz")
    np.savez(npz, **arrays)

    meta = {
        "source": "TI Radar Toolbox 4.00.00.05, Gesture_with_Machine_Learning, src/6443/include/neuralnet",
        "device": "xWR6443 / xWR6843 (AOP and ODS share one model)",
        "architecture": "%d -> %d(ReLU) -> %d(ReLU) -> %d(softmax)" % (INP_DIM, L1, L2, L3),
        "input": {
            "num_features": NUM_FEATURES,
            "feature_length_frames": FEATURE_LEN,
            "layout": "oldest frame first; within a frame the 6 features are in "
                      "the order below (objectdetection.c:954-959)",
            "feature_order": [
                "weightedDoppler",        # gfeatures.pWtdoppler
                "weightedAzimuthMean",    # gfeatures.pWtaz_mean
                "weightedElevationMean",  # gfeatures.pWtel_mean
                "numDetections",          # gfeatures.pNumDetections
                "weightedRange",          # gfeatures.pWtrange
                "azimuthDopplerCorr",     # gfeatures.pAzdoppcorr
            ],
            "normalisation": "(x - mean[k]) / std[k], per feature k, applied "
                             "before the value enters the 15-frame window",
        },
        "classes": [
            "no_gesture", "l2r", "r2l", "u2d", "d2u",
            "twirl_cw", "twirl_ccw", "off_push", "on_pull", "shine",
        ],
        "mean": arrays["mean"].tolist(),
        "std": arrays["std"].tolist(),
    }
    js = os.path.join(out_dir, "ti_ann_6843.json")
    with open(js, "w") as f:
        json.dump(meta, f, indent=2)
    return npz


if __name__ == "__main__":
    hdr = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HEADERS
    p = export(hdr)
    d = np.load(p)
    for k in d.files:
        a = d[k]
        print("%-5s %-12s min %+8.4f  max %+8.4f  mean %+8.4f"
              % (k, a.shape, a.min(), a.max(), a.mean()))
    print("\nwrote", p)
