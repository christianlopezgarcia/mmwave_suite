"""
plotting/pointcloud.py -- point-cloud views, ported from dat_parser_plots_v2.

These are NOT superseded by the dense RT/VT maps in plotting/maps.py -- they
answer a different question, and keeping both is deliberate.

    maps.py        every range-Doppler CELL, whether or not anything was
                   detected there. Shows the signal, including what CFAR
                   rejected. Dense, continuous, needs a tier-B capture to be
                   meaningful in Doppler.

    pointcloud.py  only the points the radar DECIDED were targets, each carrying
                   x/y/z, velocity, SNR and noise. Sparse, but every mark is a
                   detection with a full attribute set attached.

A dense map cannot tell you "the detector fired here with 28 dB SNR at +40°
azimuth". A point cloud cannot tell you "there was energy here that fell 2 dB
short of the threshold". You want both.

Two changes from the original:
  * They take a `keep_mask`, so a ghost method visibly changes them. The
    originals had no filtering concept.
  * SNR is decoded as SIGNED int16 x 0.1 dB. The original path read it via TI's
    reference parser, which reads it unsigned -- turning any negative SNR into
    ~6553 dB.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

import numpy as np

# Original styling, kept so figures stay comparable with earlier work.
CMAP = "turbo"
MD_GAMMA = 0.4      # PowerNorm on the micro-Doppler histogram
ANG_GAMMA = 0.5
BINS_T = 350
BINS_V = 150
BINS_R = 200
BINS_A = 180


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


def _sel(f: Dict[str, np.ndarray], keep_mask: Optional[np.ndarray]):
    """Apply a ghost mask and drop non-finite rows."""
    n = f["t"].size
    m = np.ones(n, bool) if keep_mask is None or keep_mask.size != n else keep_mask.copy()
    return {k: v[m] for k, v in f.items()}


def _guard(ax, x) -> bool:
    """Histogram functions raise on empty input; say so on the axes instead."""
    if x.size >= 2:
        return True
    ax.text(0.5, 0.5, "too few points to plot (%d)" % x.size, ha="center",
            va="center", transform=ax.transAxes, color="0.4")
    return False


# ---------------------------------------------------------------------------
# Single-panel
# ---------------------------------------------------------------------------


def micro_doppler(f, save_path=None, keep_mask=None, dpi=200):
    """Time-velocity density. The point-cloud analogue of a VT spectrogram.

    PowerNorm(gamma=0.4) compresses the dynamic range so the sparse high-|v|
    limb detections stay visible next to the dense near-zero torso and clutter
    band -- on a linear scale the zero-Doppler line saturates everything else.
    """
    import matplotlib.pyplot as plt
    import matplotlib.colors as mc
    d = _sel(f, keep_mask)
    fig, ax = plt.subplots(figsize=(12, 5))
    if _guard(ax, d["t"]):
        h = ax.hist2d(d["t"], d["v"], bins=[BINS_T, BINS_V], cmap=CMAP,
                      norm=mc.PowerNorm(gamma=MD_GAMMA))
        fig.colorbar(h[3], ax=ax, label="detection density")
    ax.set_xlabel("Time (s)"); ax.set_ylabel("Velocity (m/s)")
    ax.set_title("Micro-Doppler signature (point cloud)")
    ax.grid(alpha=0.2)
    return _finish(fig, save_path, dpi)


def range_time_intensity(f, save_path=None, keep_mask=None, range_max=10.0, dpi=200):
    """Time-range density on a LOG colour scale.

    LogNorm because a stationary wall produces orders of magnitude more
    detections in one range bin than a walking person does across many.
    """
    import matplotlib.pyplot as plt
    import matplotlib.colors as mc
    d = _sel(f, keep_mask)
    fig, ax = plt.subplots(figsize=(12, 5))
    if _guard(ax, d["t"]):
        h = ax.hist2d(d["t"], d["range"], bins=[BINS_T, BINS_R], cmap=CMAP,
                      norm=mc.LogNorm())
        fig.colorbar(h[3], ax=ax, label="detection density (log)")
    ax.set_xlabel("Time (s)"); ax.set_ylabel("Range (m)")
    ax.set_ylim(0, range_max)
    ax.set_title("Range-Time intensity (point cloud)")
    return _finish(fig, save_path, dpi)


def angle_time_intensity(f, which="azimuth", save_path=None, keep_mask=None, dpi=200):
    """Time-angle density. `which` is 'azimuth' or 'elevation'.

    This is the view a dense RT/VT map cannot give you at all, and the one that
    matters most for multipath: a specular ghost sits at a DIFFERENT bearing
    from its parent, so a mirrored pair of tracks here is the signature.
    """
    import matplotlib.pyplot as plt
    import matplotlib.colors as mc
    key = "azimuth_deg" if which == "azimuth" else "elevation_deg"
    d = _sel(f, keep_mask)
    fig, ax = plt.subplots(figsize=(12, 5))
    if _guard(ax, d["t"]):
        h = ax.hist2d(d["t"], d[key], bins=[BINS_T, BINS_A], cmap=CMAP,
                      norm=mc.PowerNorm(gamma=ANG_GAMMA))
        fig.colorbar(h[3], ax=ax, label="detection density")
    ax.set_xlabel("Time (s)"); ax.set_ylabel("%s (deg)" % which.capitalize())
    ax.set_title("%s-Time intensity (point cloud)" % which.capitalize())
    ax.grid(alpha=0.2)
    return _finish(fig, save_path, dpi)


def _scatter(f, ykey, ylabel, title, save_path, keep_mask, colour_by=None,
             cmap="viridis", ylim=None, dpi=200):
    import matplotlib.pyplot as plt
    d = _sel(f, keep_mask)
    fig, ax = plt.subplots(figsize=(10, 5))
    c = d[colour_by] if colour_by else None
    sc = ax.scatter(d["t"], d[ykey], s=2, c=c, cmap=cmap if c is not None else None,
                    alpha=0.45, rasterized=True)
    if c is not None and d["t"].size:
        fig.colorbar(sc, ax=ax, label=ylabel)
    ax.set_xlabel("Time (s)"); ax.set_ylabel(ylabel); ax.set_title(title)
    if ylim:
        ax.set_ylim(*ylim)
    ax.grid(alpha=0.25)
    return _finish(fig, save_path, dpi)


def snr_vs_time(f, save_path=None, keep_mask=None, dpi=200):
    """Per-detection SNR over time. Signed, in dB.

    Use it to sanity-check a CFAR threshold: with `cfarCfg ... 15` nothing below
    ~11 dB should appear. A cloud hugging the lower edge means the threshold is
    doing most of the work and weak multipath is being cut.
    """
    return _scatter(f, "snr_db", "SNR (dB)", "SNR vs time", save_path,
                    keep_mask, colour_by="snr_db", cmap="plasma", dpi=dpi)


def range_vs_time(f, save_path=None, keep_mask=None, dpi=200):
    """Raw range trajectories. The clearest view of who moved where."""
    return _scatter(f, "range", "Range (m)", "Range vs time", save_path,
                    keep_mask, dpi=dpi)


def velocity_vs_time(f, save_path=None, keep_mask=None, dpi=200):
    """Raw velocity, coloured by velocity. Shows sign reversal at turnarounds."""
    return _scatter(f, "v", "Velocity (m/s)", "Velocity vs time", save_path,
                    keep_mask, colour_by="v", cmap="viridis", dpi=dpi)


# ---------------------------------------------------------------------------
# Two-panel comparisons -- shared time axis
# ---------------------------------------------------------------------------


def _pair(f, save_path, keep_mask, top, bottom, suptitle, dpi=200):
    """`top`/`bottom` are (kind, key, label, kwargs) where kind is
    'hist' or 'scatter'.

    Colorbars are attached via make_axes_locatable rather than fig.colorbar.
    fig.colorbar STEALS width from the axes it is attached to, so a histogram
    panel (which has one) ends up narrower than a scatter panel (which does
    not) -- and with sharex the two x-axes then no longer line up, which is
    exactly the misalignment this replaces. Appending a fixed-width cax to
    EVERY axes and hiding the unused ones keeps all panels identical.
    """
    import matplotlib.pyplot as plt
    from mpl_toolkits.axes_grid1 import make_axes_locatable
    d = _sel(f, keep_mask)
    fig, axes = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    for ax, (kind, key, label, kw) in zip(axes, (top, bottom)):
        div = make_axes_locatable(ax)
        cax = div.append_axes("right", size="2%", pad=0.08)
        if not _guard(ax, d["t"]):
            ax.set_ylabel(label)
            cax.set_axis_off()
            continue
        if kind == "hist":
            h = ax.hist2d(d["t"], d[key], bins=kw.get("bins", [BINS_T, BINS_A]),
                          cmap=CMAP, norm=kw.get("norm"))
            fig.colorbar(h[3], cax=cax, label="density")
        else:
            ax.scatter(d["t"], d[key], s=2, color=kw.get("color", "tab:blue"),
                       alpha=0.4, rasterized=True)
            cax.set_axis_off()          # reserve the width, draw nothing
        ax.set_ylabel(label)
        if kw.get("ylim"):
            ax.set_ylim(*kw["ylim"])
        ax.grid(alpha=0.22)
    axes[1].set_xlabel("Time (s)")
    fig.suptitle(suptitle, y=0.995)
    fig.tight_layout()
    return _finish(fig, save_path, dpi)


def compare_velocity_range(f, save_path=None, keep_mask=None, dpi=200):
    """Velocity above, range below. Reads a walk cycle directly: velocity
    crosses zero exactly where range reverses."""
    import matplotlib.colors as mc
    return _pair(f, save_path, keep_mask,
                 ("scatter", "v", "Velocity (m/s)", dict(color="tab:cyan")),
                 ("scatter", "range", "Range (m)", dict(color="tab:blue")),
                 "Velocity (top) vs Range (bottom)", dpi)


def compare_snr_range(f, save_path=None, keep_mask=None, dpi=200):
    """SNR above, range below. Ghost-hunting view: a genuine multipath return is
    BOTH farther AND weaker, so look for the two panels moving oppositely."""
    return _pair(f, save_path, keep_mask,
                 ("scatter", "snr_db", "SNR (dB)", dict(color="tab:orange")),
                 ("scatter", "range", "Range (m)", dict(color="tab:red")),
                 "SNR (top) vs Range (bottom)", dpi)


def compare_micro_doppler_range(f, save_path=None, keep_mask=None, dpi=200):
    """Micro-Doppler density above, range scatter below. Ties a limb-velocity
    burst to the range the subject was at when it happened."""
    import matplotlib.colors as mc
    return _pair(f, save_path, keep_mask,
                 ("hist", "v", "Velocity (m/s)",
                  dict(bins=[BINS_T, BINS_V], norm=mc.PowerNorm(gamma=MD_GAMMA))),
                 ("scatter", "range", "Range (m)", dict(color="black")),
                 "Micro-Doppler (top) vs Range (bottom)", dpi)


def compare_azimuth_range(f, save_path=None, keep_mask=None, dpi=200):
    """Azimuth density above, range below. **The most useful for ghosts.**
    A real target and its specular image share a time signature but sit at
    different bearings -- two angular tracks, one range trajectory."""
    import matplotlib.colors as mc
    return _pair(f, save_path, keep_mask,
                 ("hist", "azimuth_deg", "Azimuth (deg)",
                  dict(bins=[BINS_T, BINS_A], norm=mc.PowerNorm(gamma=ANG_GAMMA))),
                 ("scatter", "range", "Range (m)", dict(color="tab:green")),
                 "Azimuth (top) vs Range (bottom)", dpi)


def compare_elevation_range(f, save_path=None, keep_mask=None, dpi=200):
    """Elevation density above, range below. A floor- or ceiling-bounce ghost is
    displaced in ELEVATION rather than azimuth -- this is where it shows."""
    import matplotlib.colors as mc
    return _pair(f, save_path, keep_mask,
                 ("hist", "elevation_deg", "Elevation (deg)",
                  dict(bins=[BINS_T, BINS_A], norm=mc.PowerNorm(gamma=ANG_GAMMA))),
                 ("scatter", "range", "Range (m)", dict(color="tab:purple")),
                 "Elevation (top) vs Range (bottom)", dpi)


def compare_rti_range(f, save_path=None, keep_mask=None, range_max=10.0, dpi=200):
    """The same range data as a density map and as a scatter.

    Worth having both: the density map hides how FEW points make a faint track,
    and the scatter hides how INTENSE a bright one is. Seeing them together
    stops you over-reading a heat map built from a handful of detections."""
    import matplotlib.colors as mc
    return _pair(f, save_path, keep_mask,
                 ("hist", "range", "Range (m), density",
                  dict(bins=[BINS_T, BINS_R], norm=mc.LogNorm(),
                       ylim=(0, range_max))),
                 ("scatter", "range", "Range (m), scatter",
                  dict(color="tab:orange", ylim=(0, range_max))),
                 "Range density (top) vs Range scatter (bottom)", dpi)


# ---------------------------------------------------------------------------


def plot_all(rec, out_dir: str, keep_mask=None, tag: str = "", dpi: int = 200):
    """Write all 12 point-cloud figures. Returns the list of paths."""
    f = rec.flat()
    rmax = max(2.0, float(np.nanpercentile(f["range"], 99.5)) * 1.15) \
        if f["range"].size else 10.0
    sfx = ("_" + tag) if tag else ""
    jobs = [
        ("micro_doppler",            lambda p: micro_doppler(f, p, keep_mask, dpi)),
        ("range_time_intensity",     lambda p: range_time_intensity(f, p, keep_mask, rmax, dpi)),
        ("azimuth_time_intensity",   lambda p: angle_time_intensity(f, "azimuth", p, keep_mask, dpi)),
        ("elevation_time_intensity", lambda p: angle_time_intensity(f, "elevation", p, keep_mask, dpi)),
        ("snr_vs_time",              lambda p: snr_vs_time(f, p, keep_mask, dpi)),
        ("range_vs_time",            lambda p: range_vs_time(f, p, keep_mask, dpi)),
        ("velocity_vs_time",         lambda p: velocity_vs_time(f, p, keep_mask, dpi)),
        ("compare_velocity_range",   lambda p: compare_velocity_range(f, p, keep_mask, dpi)),
        ("compare_snr_range",        lambda p: compare_snr_range(f, p, keep_mask, dpi)),
        ("compare_micro_doppler_range", lambda p: compare_micro_doppler_range(f, p, keep_mask, dpi)),
        ("compare_azimuth_range",    lambda p: compare_azimuth_range(f, p, keep_mask, dpi)),
        ("compare_elevation_range",  lambda p: compare_elevation_range(f, p, keep_mask, dpi)),
        ("compare_rti_range",        lambda p: compare_rti_range(f, p, keep_mask, rmax, dpi)),
    ]
    out = []
    for name, fn in jobs:
        p = os.path.join(out_dir, "%s%s.png" % (name, sfx))
        try:
            out.append(fn(p))
        except Exception as e:               # one bad figure must not kill the run
            print("   (point-cloud figure %s failed: %r)" % (name, e))
    return out
