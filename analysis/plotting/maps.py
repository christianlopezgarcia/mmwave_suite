"""
plotting/maps.py -- RT and VT figures.

Deliberately matches the reference paper's conventions rather than a modern
palette: `jet` colormap, `imagesc` orientation (origin lower), and colour limits
from the 35th/99.5th percentiles, exactly as `run_fixed_two_person.m` does
(`pct = @(M,lo,hi) [prctile(M(:),lo), prctile(M(:),hi)]`).

That is a considered choice, not an oversight. `jet` is a poor perceptual
colormap and is not colourblind-safe, but these figures have to sit beside
already-published ones from the same group. Pass `cmap='turbo'` for a
perceptually-better near-drop-in, or `cmap='viridis'` if a figure is going
somewhere that does not need to match.

Every figure stamps its map's provenance into the subtitle, so a plot can always
be traced back to the tier and processing that produced it.
"""

from __future__ import annotations

import os
import textwrap
from typing import Optional, Sequence, Tuple

import matplotlib
import numpy as np

from ..core.maps import TimeMap

PAPER_CMAP = "jet"
PAPER_CLIP = (35.0, 99.5)


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)


def _finish(fig, save_path: Optional[str], dpi: int = 200):
    if save_path:
        _ensure_dir(save_path)
        fig.savefig(save_path, dpi=dpi, bbox_inches="tight")
        matplotlib.pyplot.close(fig)
        return save_path
    return fig


def _subtitle(ax, m: TimeMap) -> None:
    ax.text(0.0, -0.22, textwrap.fill("source: " + m.provenance, 110),
            transform=ax.transAxes, fontsize=6.5, color="0.35",
            va="top", ha="left", family="monospace")


def plot_time_map(m: TimeMap,
                  title: str,
                  save_path: Optional[str] = None,
                  cmap: str = PAPER_CMAP,
                  clip: Tuple[float, float] = PAPER_CLIP,
                  ylim: Optional[Tuple[float, float]] = None,
                  overlays: Optional[Sequence[dict]] = None,
                  figsize: Tuple[float, float] = (14, 5),
                  dpi: int = 200):
    """Render an RT or VT map.

    overlays: list of {'t':…, 'y':…, 'label':…, 'style':…} drawn on top, used
    for target tracks and detected-step markers.
    """
    import matplotlib.pyplot as plt

    vmin, vmax = m.clip_percentile(*clip)
    if not np.isfinite([vmin, vmax]).all() or vmin >= vmax:
        finite = m.data[np.isfinite(m.data)]
        vmin, vmax = ((float(finite.min()), float(finite.max()))
                      if finite.size else (0.0, 1.0))
        if vmin >= vmax:
            vmax = vmin + 1.0

    fig, ax = plt.subplots(figsize=figsize)
    extent = [float(m.t[0]), float(m.t[-1]),
              float(m.axis[0]), float(m.axis[-1])]
    im = ax.imshow(m.data, aspect="auto", origin="lower", cmap=cmap,
                   extent=extent, vmin=vmin, vmax=vmax, interpolation="nearest")
    fig.colorbar(im, ax=ax, label=m.value_label, pad=0.01)

    ax.set_xlabel("Time (s)")
    ax.set_ylabel(m.axis_label)
    ax.set_title(title)
    if ylim:
        ax.set_ylim(*ylim)

    for ov in overlays or []:
        ax.plot(ov["t"], ov["y"], ov.get("style", "w-"),
                lw=ov.get("lw", 1.6), label=ov.get("label"),
                alpha=ov.get("alpha", 0.95))
    if overlays and any(o.get("label") for o in overlays):
        ax.legend(loc="upper right", fontsize=8, framealpha=0.7)

    _subtitle(ax, m)
    return _finish(fig, save_path, dpi)


def plot_rt(m: TimeMap, save_path=None, title="Range-Time (RT)", **kw):
    return plot_time_map(m, title, save_path, **kw)


def plot_vt(m: TimeMap, save_path=None, title="Velocity-Time (VT)", **kw):
    return plot_time_map(m, title, save_path, **kw)


def plot_rt_vt_pair(rt: TimeMap, vt: TimeMap,
                    title: str = "",
                    save_path: Optional[str] = None,
                    cmap: str = PAPER_CMAP,
                    clip: Tuple[float, float] = PAPER_CLIP,
                    rt_ylim: Optional[Tuple[float, float]] = None,
                    dpi: int = 200):
    """RT above VT on a shared time axis -- the paper's Figure 3/4 layout."""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
    for ax, m, ttl in ((axes[0], rt, "RT"), (axes[1], vt, "VT")):
        vmin, vmax = m.clip_percentile(*clip)
        if not np.isfinite([vmin, vmax]).all() or vmin >= vmax:
            vmin, vmax = 0.0, 1.0
        im = ax.imshow(m.data, aspect="auto", origin="lower", cmap=cmap,
                       extent=[float(m.t[0]), float(m.t[-1]),
                               float(m.axis[0]), float(m.axis[-1])],
                       vmin=vmin, vmax=vmax, interpolation="nearest")
        fig.colorbar(im, ax=ax, label=m.value_label, pad=0.01)
        ax.set_ylabel(m.axis_label)
        ax.set_title("%s -- %s" % (ttl, m.tier), fontsize=9, loc="left")
    if rt_ylim:
        axes[0].set_ylim(*rt_ylim)
    axes[1].set_xlabel("Time (s)")
    if title:
        fig.suptitle(title, y=0.995)
    _subtitle(axes[1], vt)
    fig.tight_layout()
    return _finish(fig, save_path, dpi)


def plot_range_doppler_frame(rd: np.ndarray, r_axis, v_axis,
                             title="Range-Doppler",
                             save_path=None, cmap=PAPER_CMAP, dpi=200):
    """A single RD frame -- the diagnostic for whether TLV 5 decoded correctly.

    A transposed or mis-registered map looks like structured noise here long
    before it becomes obvious in a marginal.
    """
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(rd, aspect="auto", origin="lower", cmap=cmap,
                   extent=[float(r_axis[0]), float(r_axis[-1]),
                           float(v_axis[0]), float(v_axis[-1])])
    fig.colorbar(im, ax=ax, label="power", pad=0.01)
    ax.set_xlabel("Range (m)")
    ax.set_ylabel("Velocity (m/s)")
    ax.set_title(title)
    return _finish(fig, save_path, dpi)
