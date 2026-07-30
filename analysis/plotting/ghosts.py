"""
plotting/ghosts.py -- figures for the multipath / ghost section.

The controlling idea: never show a filtered result without the unfiltered one
beside it. A ghost filter that removes 80% of points looks impressive until you
see it also removed the target. Every function here is a side-by-side or an
overlay for that reason.
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np

from ..ghost_sections.detectors import GhostLabels
from ..ghost_sections.features import PointFeatures


def _finish(fig, save_path, dpi=200):
    import matplotlib.pyplot as plt
    if save_path:
        d = os.path.dirname(os.path.abspath(save_path))
        if d:
            os.makedirs(d, exist_ok=True)
        fig.savefig(save_path, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        return save_path
    return fig


def plot_range_time_tagged(pf: PointFeatures, labels: GhostLabels,
                           title: Optional[str] = None,
                           save_path: Optional[str] = None,
                           figsize=(14, 6), dpi: int = 200,
                           range_max: Optional[float] = None):
    """Range vs time, kept points and tagged points in one axes."""
    import matplotlib.pyplot as plt
    keep = ~labels.is_ghost
    fig, ax = plt.subplots(figsize=figsize)
    ax.scatter(pf.t[keep], pf.range[keep], s=4, c="tab:blue", alpha=0.55,
               rasterized=True, label="kept (n=%d)" % int(keep.sum()))
    ax.scatter(pf.t[labels.is_ghost], pf.range[labels.is_ghost], s=9,
               c="crimson", alpha=0.75, marker="x", rasterized=True,
               label="tagged ghost (n=%d)" % labels.n_ghosts)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Range (m)")
    ax.set_title(title or ("Range-Time -- %s" % labels.name))
    if range_max:
        ax.set_ylim(0, range_max)
    ax.grid(alpha=0.25)
    ax.legend(loc="upper right", fontsize=8)
    fig.text(0.005, -0.04, "criterion: " + labels.description, fontsize=7,
             color="0.35", family="monospace")
    return _finish(fig, save_path, dpi)


def plot_before_after(pf: PointFeatures, labels: GhostLabels,
                      save_path: Optional[str] = None,
                      figsize=(15, 9), dpi: int = 200,
                      range_max: Optional[float] = None):
    """Two-by-two: range-time and velocity-time, raw vs filtered.

    The bottom row is what your ghost-filtered analysis would actually see; the
    top row is what the device reported. If a real trajectory disappears between
    the rows, the method is over-filtering and no summary statistic will say so.
    """
    import matplotlib.pyplot as plt
    keep = ~labels.is_ghost
    fig, ax = plt.subplots(2, 2, figsize=figsize, sharex=True)

    ax[0, 0].scatter(pf.t, pf.range, s=4, c="0.35", alpha=0.5, rasterized=True)
    ax[0, 0].set_title("RAW -- all %d points" % len(pf), fontsize=10)
    ax[0, 0].set_ylabel("Range (m)")

    ax[0, 1].scatter(pf.t, pf.v, s=4, c="0.35", alpha=0.5, rasterized=True)
    ax[0, 1].set_title("RAW -- velocity", fontsize=10)
    ax[0, 1].set_ylabel("Velocity (m/s)")

    ax[1, 0].scatter(pf.t[keep], pf.range[keep], s=4, c="tab:blue",
                     alpha=0.6, rasterized=True)
    ax[1, 0].set_title("FILTERED -- %d kept, %d removed (%.1f%%)"
                       % (int(keep.sum()), labels.n_ghosts,
                          100 * labels.fraction), fontsize=10)
    ax[1, 0].set_ylabel("Range (m)")
    ax[1, 0].set_xlabel("Time (s)")

    ax[1, 1].scatter(pf.t[keep], pf.v[keep], s=4, c="tab:blue", alpha=0.6,
                     rasterized=True)
    ax[1, 1].set_title("FILTERED -- velocity", fontsize=10)
    ax[1, 1].set_ylabel("Velocity (m/s)")
    ax[1, 1].set_xlabel("Time (s)")

    if range_max:
        ax[0, 0].set_ylim(0, range_max)
        ax[1, 0].set_ylim(0, range_max)
    for a in ax.ravel():
        a.grid(alpha=0.22)

    fig.suptitle("%s -- %s" % (labels.name, labels.description), y=0.995)
    fig.tight_layout()
    return _finish(fig, save_path, dpi)


def plot_feature_space(pf: PointFeatures, labels: GhostLabels,
                       save_path: Optional[str] = None,
                       figsize=(15, 4.5), dpi: int = 200):
    """The three discriminating features, tagged points highlighted.

    Shows whether a method is exploiting the physics it claims to: if the tagged
    cloud is not separated in any of these panels, the criterion is arbitrary.
    """
    import matplotlib.pyplot as plt
    g = labels.is_ghost
    fig, ax = plt.subplots(1, 3, figsize=figsize)

    panels = [
        (pf.d_range_to_primary, pf.snr_excess_db,
         r"$\Delta$range to primary (m)", "SNR excess (dB)",
         "amplitude vs geometry"),
        (pf.d_azimuth_to_primary, pf.d_v_to_primary,
         r"$\Delta$azimuth to primary (deg)", r"$\Delta v$ to primary (m/s)",
         "angular vs Doppler offset"),
        (pf.range, pf.v, "Range (m)", "Velocity (m/s)", "range-Doppler"),
    ]
    for a, (X, Y, xl, yl, ttl) in zip(ax, panels):
        if X is None or Y is None:
            a.set_visible(False)
            continue
        a.scatter(X[~g], Y[~g], s=4, c="0.6", alpha=0.45, rasterized=True,
                  label="kept")
        a.scatter(X[g], Y[g], s=8, c="crimson", alpha=0.7, rasterized=True,
                  label="tagged")
        a.set_xlabel(xl)
        a.set_ylabel(yl)
        a.set_title(ttl, fontsize=9)
        a.grid(alpha=0.22)
    ax[0].legend(loc="best", fontsize=8)
    fig.suptitle("Feature space -- %s" % labels.name, y=1.02)
    fig.tight_layout()
    return _finish(fig, save_path, dpi)


def plot_method_comparison(pf: PointFeatures, labels_by_name: dict,
                           save_path: Optional[str] = None,
                           figsize=(13, 5), dpi: int = 200):
    """How much each method removes, and how much they overlap."""
    import matplotlib.pyplot as plt
    names = list(labels_by_name)
    fracs = [100 * labels_by_name[n].fraction for n in names]

    fig, ax = plt.subplots(1, 2, figsize=figsize)
    ax[0].barh(np.arange(len(names)), fracs, color="tab:red", alpha=0.75)
    ax[0].set_yticks(np.arange(len(names)))
    ax[0].set_yticklabels(names, fontsize=8)
    ax[0].set_xlabel("% of points tagged as ghost")
    ax[0].grid(alpha=0.25, axis="x")
    ax[0].invert_yaxis()

    n = len(names)
    M = np.zeros((n, n))
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            A = labels_by_name[a].is_ghost
            B = labels_by_name[b].is_ghost
            u = (A | B).sum()
            M[i, j] = (A & B).sum() / u if u else 0.0
    im = ax[1].imshow(M, cmap="viridis", vmin=0, vmax=1)
    ax[1].set_xticks(np.arange(n)); ax[1].set_yticks(np.arange(n))
    ax[1].set_xticklabels([s.split("_")[0] for s in names], rotation=45,
                          ha="right", fontsize=8)
    ax[1].set_yticklabels([s.split("_")[0] for s in names], fontsize=8)
    ax[1].set_title("pairwise Jaccard overlap", fontsize=9)
    for i in range(n):
        for j in range(n):
            ax[1].text(j, i, "%.2f" % M[i, j], ha="center", va="center",
                       fontsize=7,
                       color="white" if M[i, j] < 0.6 else "black")
    fig.colorbar(im, ax=ax[1], pad=0.02)
    fig.tight_layout()
    return _finish(fig, save_path, dpi)
