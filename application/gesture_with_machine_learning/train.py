"""Retrain the gesture MLP, in numpy, into the same .npz the runtime loads.

Pure numpy on purpose.  The network is 90-30-60-10: about 4400 parameters and
a few thousand training windows.  Adam on the full architecture converges in
seconds on a laptop CPU, and keeping it dependency-free means the runtime and
the trainer can never disagree about what the model is -- `ann.py` reads
exactly the arrays this writes, and the forward pass here is the same code
path the board's C implements.

You need this whenever the features did not come from TI's firmware; see the
note at the top of features.py for why the shipped weights do not transfer.

    python -m mmwave_suite.application.gesture_with_machine_learning.train \
        --data data/gestures --out model/my_ann.npz

Then point the pipeline at it:

    GesturePipeline(mode="points", model_path="model/my_ann.npz")
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Optional, Sequence, Tuple

import numpy as np

from .ann import CLASS_NAMES, FEATURE_LEN, NUM_FEATURES, ANN_FEATURE_ORDER
from .dataset import (apply_normalisation, fit_normalisation, load_sessions,
                      windows)

L1, L2 = 30, 60


def _init(rng, fan_in, fan_out):
    """He initialisation -- the hidden layers are ReLU."""
    return rng.normal(0.0, np.sqrt(2.0 / fan_in), size=(fan_out, fan_in))


def _forward(p, x):
    z1 = x @ p["w0"].T + p["b0"]
    a1 = np.maximum(z1, 0.0)
    z2 = a1 @ p["w1"].T + p["b1"]
    a2 = np.maximum(z2, 0.0)
    z3 = a2 @ p["w2"].T + p["b2"]
    z3 = z3 - z3.max(axis=1, keepdims=True)
    e = np.exp(z3)
    return e / e.sum(axis=1, keepdims=True), (z1, a1, z2, a2)


def _backward(p, x, y_onehot, probs, cache):
    z1, a1, z2, a2 = cache
    n = x.shape[0]
    d3 = (probs - y_onehot) / n                    # softmax + cross-entropy
    g = {"w2": d3.T @ a2, "b2": d3.sum(axis=0)}
    d2 = (d3 @ p["w2"]) * (z2 > 0)
    g["w1"] = d2.T @ a1
    g["b1"] = d2.sum(axis=0)
    d1 = (d2 @ p["w1"]) * (z1 > 0)
    g["w0"] = d1.T @ x
    g["b0"] = d1.sum(axis=0)
    return g


def train(X: np.ndarray,
          y: np.ndarray,
          n_classes: int = len(CLASS_NAMES),
          epochs: int = 300,
          batch: int = 128,
          lr: float = 3e-3,
          weight_decay: float = 1e-4,
          val_frac: float = 0.2,
          class_balance: bool = True,
          seed: int = 0,
          verbose: bool = True) -> Tuple[dict, dict]:
    rng = np.random.default_rng(seed)
    n, d = X.shape

    # Split by shuffled index.  NOTE: windows from one session overlap
    # heavily, so a random split leaks between train and val and the reported
    # accuracy is optimistic.  For a number you can trust, record a separate
    # take and replay it with run_offline -- see the closing note in main().
    order = rng.permutation(n)
    n_val = int(n * val_frac)
    val, tr = order[:n_val], order[n_val:]
    Xtr, ytr, Xva, yva = X[tr], y[tr], X[val], y[val]

    w = np.ones(n_classes)
    if class_balance:
        cnt = np.bincount(ytr, minlength=n_classes).astype(np.float64)
        w = np.where(cnt > 0, cnt.sum() / np.maximum(cnt, 1) / n_classes, 0.0)

    p = {
        "w0": _init(rng, d, L1), "b0": np.zeros(L1),
        "w1": _init(rng, L1, L2), "b1": np.zeros(L2),
        "w2": _init(rng, L2, n_classes), "b2": np.zeros(n_classes),
    }
    m = {k: np.zeros_like(v) for k, v in p.items()}
    v = {k: np.zeros_like(x) for k, x in p.items()}
    b1, b2, eps, step = 0.9, 0.999, 1e-8, 0

    onehot_tr = np.eye(n_classes)[ytr] * w[ytr][:, None]
    best = {"acc": -1.0, "epoch": -1, "params": None}
    hist = []

    for ep in range(epochs):
        idx = rng.permutation(Xtr.shape[0])
        for s in range(0, idx.size, batch):
            b = idx[s:s + batch]
            probs, cache = _forward(p, Xtr[b])
            g = _backward(p, Xtr[b], onehot_tr[b], probs, cache)
            step += 1
            for k in p:
                if k.startswith("w"):
                    g[k] = g[k] + weight_decay * p[k]
                m[k] = b1 * m[k] + (1 - b1) * g[k]
                v[k] = b2 * v[k] + (1 - b2) * g[k] ** 2
                mh = m[k] / (1 - b1 ** step)
                vh = v[k] / (1 - b2 ** step)
                p[k] = p[k] - lr * mh / (np.sqrt(vh) + eps)

        if Xva.shape[0]:
            pv, _ = _forward(p, Xva)
            acc = float((pv.argmax(axis=1) == yva).mean())
        else:
            pt, _ = _forward(p, Xtr)
            acc = float((pt.argmax(axis=1) == ytr).mean())
        hist.append(acc)
        if acc > best["acc"]:
            best = {"acc": acc, "epoch": ep,
                    "params": {k: x.copy() for k, x in p.items()}}
        if verbose and (ep % 25 == 0 or ep == epochs - 1):
            print("  epoch %3d  val acc %.3f  (best %.3f @ %d)"
                  % (ep, acc, best["acc"], best["epoch"]))

    params = best["params"] or p
    report = {"best_val_acc": best["acc"], "best_epoch": best["epoch"],
              "history": hist, "n_train": int(Xtr.shape[0]),
              "n_val": int(Xva.shape[0])}
    if Xva.shape[0]:
        pv, _ = _forward(params, Xva)
        cm = np.zeros((n_classes, n_classes), dtype=int)
        for t, q in zip(yva, pv.argmax(axis=1)):
            cm[t, q] += 1
        report["confusion"] = cm.tolist()
    return params, report


def save_model(path: str, params: dict, mean: np.ndarray, std: np.ndarray,
               feature_order: Sequence[str], length: int,
               class_names: Sequence[str], report: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    np.savez(path,
             mean=mean.astype(np.float32), std=std.astype(np.float32),
             w0=params["w0"].astype(np.float32), b0=params["b0"].astype(np.float32),
             w1=params["w1"].astype(np.float32), b1=params["b1"].astype(np.float32),
             w2=params["w2"].astype(np.float32), b2=params["b2"].astype(np.float32))
    meta = {
        "source": "retrained by mmwave_suite.application."
                  "gesture_with_machine_learning.train",
        "architecture": "%d -> %d(ReLU) -> %d(ReLU) -> %d(softmax)"
                        % (params["w0"].shape[1], L1, L2, len(class_names)),
        "input": {
            "num_features": len(feature_order),
            "feature_length_frames": length,
            "layout": "oldest frame first",
            "feature_order": list(feature_order),
            "normalisation": "(x - mean[k]) / std[k] per feature, on entry",
        },
        "classes": list(class_names),
        "mean": mean.tolist(),
        "std": std.tolist(),
        "training": {k: v for k, v in report.items() if k != "history"},
    }
    with open(os.path.splitext(path)[0] + ".json", "w") as f:
        json.dump(meta, f, indent=2)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True,
                    help="directory of .npz sessions from dataset.FeatureRecorder")
    ap.add_argument("--out", default="model/retrained_ann.npz")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--motion-quantile", type=float, default=0.6,
                    help="fraction of each take relabelled no_gesture (0.6 = "
                         "keep the most active 40%% as positives)")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)

    sessions = load_sessions(a.data)
    if not sessions:
        print("no .npz sessions under %s" % a.data)
        return 2
    print("%d sessions:" % len(sessions))
    for s in sessions:
        print("  %-40s %-12s %d frames"
              % (os.path.basename(s["path"]), s["label"], s["features"].shape[0]))

    X, y, info = windows(sessions, stride=a.stride,
                         motion_quantile=a.motion_quantile)
    print("\n%d windows, %d inputs each" % X.shape)
    for k, v in sorted(info["counts"].items(), key=lambda kv: -kv[1]):
        print("  %-12s %d" % (k, v))

    mean, std = fit_normalisation(X, info["n_features"], info["length"])
    Xn = apply_normalisation(X, mean, std, info["n_features"], info["length"])
    print("\nnormalisation fitted over this corpus, not TI's:")
    for n, m_, s_ in zip(info["feature_order"], mean, std):
        print("  %-24s mean %+10.4f  std %10.4f" % (n, m_, s_))

    print("\ntraining:")
    params, report = train(Xn, y, epochs=a.epochs, batch=a.batch, lr=a.lr,
                           seed=a.seed)
    save_model(a.out, params, mean, std, info["feature_order"], info["length"],
               CLASS_NAMES, report)
    print("\nbest val accuracy %.3f at epoch %d" % (report["best_val_acc"],
                                                    report["best_epoch"]))
    print("NOTE: windows overlap within a take, so this split leaks and the")
    print("      number above is optimistic. Judge the model on a take you")
    print("      recorded separately, replayed with run_offline.")
    print("wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
