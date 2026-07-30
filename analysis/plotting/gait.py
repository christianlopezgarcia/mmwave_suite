"""
plotting/gait.py -- foot-velocity envelope, step markers, gait summaries.

Matches the paper's Figure 3(c) / 4(c): the envelope as a line, the v_min
threshold as a dashed rule, detected steps as red markers, and automatically
detected walking segments as shaded bands.
"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence

import numpy as np

from ..micro_doppler.envelope import Envelope
from ..micro_doppler.gait import GaitMetrics
from ..micro_doppler.steps import StepEvents


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


def plot_envelope(env: Envelope,
                  steps: Optional[StepEvents] = None,
                  metrics: Optional[GaitMetrics] = None,
                  v_min: float = 0.20,
                  title: str = "Foot-velocity envelope",
                  save_path: Optional[str] = None,
                  figsize=(14, 4.5), dpi: int = 200):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=figsize)

    if steps and steps.segments:
        for i, s in enumerate(steps.segments):
            ax.axvspan(s.t_start, s.t_end, color="tab:green", alpha=0.12,
                       label="walking segment" if i == 0 else None)

    ax.plot(env.t, env.v, "-", color="tab:blue", lw=1.3,
            label=r"$v_{foot}(t)$")
    ax.axhline(v_min, ls="--", color="0.45", lw=1.0,
               label=r"$v_{min}$ = %.2f m/s" % v_min)

    if steps and steps.n_steps:
        ax.plot(steps.times, steps.heights, "o", color="crimson", ms=5,
                label="steps (n=%d)" % steps.n_steps, zorder=5)

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Velocity (m/s)")
    ax.set_title(title)
    ax.grid(alpha=0.25)
    ax.legend(loc="upper right", fontsize=8, ncol=2, framealpha=0.85)

    if metrics:
        ax.text(0.005, 0.97,
                "cadence %.1f steps/min\nstep time %.3f s\nCV %.1f %%"
                % (metrics.cadence_steps_per_min, metrics.mean_step_time_s,
                   metrics.cv_step_time_pct),
                transform=ax.transAxes, va="top", ha="left", fontsize=8,
                family="monospace",
                bbox=dict(fc="white", ec="0.7", alpha=0.85, pad=4))

    fig.text(0.005, -0.06, "source: " + env.provenance, fontsize=6.5,
             color="0.35", family="monospace")
    return _finish(fig, save_path, dpi)


def plot_envelope_overlay(envs: Sequence[Envelope],
                          labels: Sequence[str],
                          title: str = "Foot-velocity envelopes",
                          save_path: Optional[str] = None,
                          figsize=(14, 4.5), dpi: int = 200):
    """Several envelopes on shared axes -- e.g. per person, or per method."""
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=figsize)
    styles = ["-", "--", "-.", ":"]
    for i, (e, lab) in enumerate(zip(envs, labels)):
        ax.plot(e.t, e.v, styles[i % len(styles)], lw=1.4, label=lab)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Velocity (m/s)")
    ax.set_title(title)
    ax.grid(alpha=0.25)
    ax.legend(loc="upper right", fontsize=8)
    return _finish(fig, save_path, dpi)


def plot_step_intervals(steps: StepEvents,
                        title: str = "Step-time sequence",
                        save_path: Optional[str] = None,
                        figsize=(14, 4), dpi: int = 200):
    """Step interval against step index, with the mean and +-1 sd band.

    Reveals the thing a single CV number hides: whether variability is uniform
    jitter or a few outliers, which distinguishes genuinely irregular gait from
    a couple of missed detections.
    """
    import matplotlib.pyplot as plt
    dt = steps.intervals()
    fig, ax = plt.subplots(figsize=figsize)
    if dt.size:
        k = np.arange(dt.size)
        mu, sd = dt.mean(), dt.std(ddof=1) if dt.size > 1 else 0.0
        ax.axhspan(mu - sd, mu + sd, color="tab:blue", alpha=0.12,
                   label=r"mean $\pm$ 1 sd")
        ax.axhline(mu, color="tab:blue", lw=1.0)
        ax.plot(k, dt, "o-", color="tab:purple", ms=4, lw=1.0,
                label=r"$\Delta t_k$")
        ax.legend(loc="upper right", fontsize=8)
    else:
        ax.text(0.5, 0.5, "no step intervals within walking segments",
                ha="center", va="center", transform=ax.transAxes, color="0.4")
    ax.set_xlabel("Step index $k$")
    ax.set_ylabel(r"$\Delta t_k$ (s)")
    ax.set_title(title)
    ax.grid(alpha=0.25)
    return _finish(fig, save_path, dpi)


def plot_gait_summary(metrics_by_name: dict,
                      title: str = "Gait metrics",
                      save_path: Optional[str] = None,
                      figsize=(12, 4.5), dpi: int = 200):
    """Grouped bars comparing cadence and CV across runs or methods."""
    import matplotlib.pyplot as plt
    names = list(metrics_by_name)
    cad = [metrics_by_name[n].cadence_steps_per_min for n in names]
    cv = [metrics_by_name[n].cv_step_time_pct for n in names]

    fig, axes = plt.subplots(1, 2, figsize=figsize)
    x = np.arange(len(names))
    axes[0].bar(x, cad, color="tab:blue")
    axes[0].set_ylabel("Cadence (steps/min)")
    axes[1].bar(x, cv, color="tab:orange")
    axes[1].set_ylabel(r"CV$_{\Delta t}$ (%)")
    for ax in axes:
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=20, ha="right", fontsize=8)
        ax.grid(alpha=0.25, axis="y")
    fig.suptitle(title)
    fig.tight_layout()
    return _finish(fig, save_path, dpi)
