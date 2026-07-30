"""
plotting/diagnostics.py -- the payloads that had no figures.

TLV 3 (noise profile), TLV 6 (stats) and TLV 9 (temperature) were decoded and
discarded. They are not decorative:

  * The NOISE PROFILE is the CFAR threshold's own reference. Plotted against the
    range profile it shows you, per range bin, how much margin a detection had.
    That is the difference between "nothing was there" and "something was there
    and the threshold rejected it" -- exactly the distinction that matters when
    a ghost goes missing.

  * STATS carries interFrameProcessingMargin. When that approaches zero the DSP
    is running out of time between frames and will start dropping them. A frame
    rate that looks fine in the log can still be silently marginal here.

  * TEMPERATURE matters over a long capture: the 60 GHz front end drifts with
    die temperature, and a slow SNR decline across a 10-minute run is more often
    thermal than physical.
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np


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


def plot_range_noise_profile(rec, save_path=None, frame_index: Optional[int] = None,
                             dpi: int = 200):
    """Range profile and noise profile together, plus the mean CFAR margin.

    The lower panel is (range profile - noise profile) averaged over time: the
    headroom a detection had in each range bin. Bins near zero are where the
    threshold is deciding, and where a weak multipath return would be lost.
    """
    import matplotlib.pyplot as plt
    if rec.range_profile is None:
        return None
    x = rec.geom.range_axis()
    rp = rec.range_profile
    npf = rec.noise_profile
    i = rec.num_frames // 2 if frame_index is None else frame_index

    fig, ax = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    ax[0].plot(x, np.nanmean(rp, axis=0), color="tab:blue", lw=1.4,
               label="range profile (mean over time)")
    ax[0].plot(x, rp[i], color="tab:blue", lw=0.8, alpha=0.45,
               label="range profile (frame %d)" % i)
    if npf is not None:
        ax[0].plot(x, np.nanmean(npf, axis=0), color="tab:green", lw=1.4,
                   label="noise profile (mean)")
    ax[0].set_ylabel("Relative power (dB)")
    ax[0].set_title("Range and noise profile -- %s" % rec.name)
    ax[0].legend(fontsize=8); ax[0].grid(alpha=0.25)

    if npf is not None:
        margin = np.nanmean(rp - npf, axis=0)
        ax[1].plot(x, margin, color="crimson", lw=1.3)
        ax[1].axhline(0, ls="--", color="0.5", lw=1.0)
        thr = rec.gating.cfar_range_threshold_db
        if thr is not None:
            ax[1].axhline(thr, ls=":", color="tab:orange", lw=1.2,
                          label="CFAR threshold %.0f dB" % thr)
            ax[1].legend(fontsize=8)
        ax[1].set_ylabel("signal - noise (dB)")
        ax[1].set_title("CFAR margin per range bin -- bins near 0 are where "
                        "detections are being decided", fontsize=9, loc="left")
    else:
        ax[1].text(0.5, 0.5, "no noise profile in this recording\n"
                             "(enable noiseProfile in guiMonitor)",
                   ha="center", va="center", transform=ax[1].transAxes,
                   color="0.4")
    ax[1].set_xlabel("Range (m)"); ax[1].grid(alpha=0.25)
    fig.tight_layout()
    return _finish(fig, save_path, dpi)


def plot_processing_stats(rec, save_path=None, dpi: int = 200):
    """CPU load and the interframe processing margin (TLV 6).

    If the margin trends toward zero the device is about to start dropping
    frames, and the capture log will not say so.
    """
    import matplotlib.pyplot as plt
    rows = [(i, s) for i, s in enumerate(rec.stats) if s]
    if not rows:
        return None
    idx = np.array([r[0] for r in rows])
    t = rec.t[idx]

    def g(k):
        return np.array([r[1].get(k, np.nan) for r in rows], dtype=float)

    fig, ax = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    ax[0].plot(t, g("active_frame_cpu_load_pct"), lw=1.2, label="active frame")
    ax[0].plot(t, g("inter_frame_cpu_load_pct"), lw=1.2, label="interframe")
    ax[0].set_ylabel("CPU load (%)"); ax[0].set_ylim(0, 100)
    ax[0].set_title("DSP load and timing margin -- %s" % rec.name)
    ax[0].legend(fontsize=8); ax[0].grid(alpha=0.25)

    margin_ms = g("inter_frame_processing_margin_us") / 1000.0
    ax[1].plot(t, margin_ms, lw=1.2, color="tab:red",
               label="interframe processing margin")
    ax[1].axhline(0, ls="--", color="0.5", lw=1.0)
    ax[1].set_ylabel("margin (ms)"); ax[1].set_xlabel("Time (s)")
    ax[1].legend(fontsize=8); ax[1].grid(alpha=0.25)
    lo = np.nanmin(margin_ms) if np.isfinite(margin_ms).any() else float("nan")
    if np.isfinite(lo):
        ax[1].set_title("minimum margin %.1f ms%s" % (
            lo, "  <-- AT RISK OF DROPPING FRAMES" if lo < 1.0 else ""),
            fontsize=9, loc="left")
    fig.tight_layout()
    return _finish(fig, save_path, dpi)


def plot_temperature(rec, save_path=None, dpi: int = 200):
    """Die temperatures over the capture (TLV 9)."""
    import matplotlib.pyplot as plt
    rows = [(i, x) for i, x in enumerate(rec.temperature) if x]
    if not rows:
        return None
    idx = np.array([r[0] for r in rows])
    t = rec.t[idx]
    fig, ax = plt.subplots(figsize=(12, 4.5))
    for key, style in [("rx0", "-"), ("rx1", "-"), ("rx2", "-"), ("rx3", "-"),
                       ("tx0", "--"), ("tx1", "--"), ("tx2", "--"),
                       ("pm", ":"), ("dig0", ":")]:
        v = np.array([r[1].get(key, np.nan) for r in rows], dtype=float)
        if np.isfinite(v).any():
            ax.plot(t, v, style, lw=1.1, label=key)
    ax.set_xlabel("Time (s)"); ax.set_ylabel("Temperature (deg C)")
    ax.set_title("Die temperatures -- %s" % rec.name)
    ax.legend(fontsize=7, ncol=5); ax.grid(alpha=0.25)
    return _finish(fig, save_path, dpi)


def plot_range_azimuth_frame(rec, frame_index: Optional[int] = None,
                             save_path=None, cmap="jet", dpi: int = 200):
    """One frame's beamformed range-azimuth map, in Cartesian coordinates.

    A bird's-eye view of the room from signal, not detections. Two returns at
    the same range but different bearing is direct ghost evidence.
    """
    import matplotlib.pyplot as plt
    from ..core.angle import range_azimuth
    if rec.angle_iq is None:
        return None
    i = rec.num_frames // 2 if frame_index is None else frame_index
    ra = range_azimuth(rec, i)
    fig, ax = plt.subplots(1, 2, figsize=(15, 6))

    im = ax[0].imshow(ra.data, aspect="auto", origin="lower", cmap=cmap,
                      extent=[ra.angle_axis[0], ra.angle_axis[-1],
                              ra.range_axis[0], ra.range_axis[-1]])
    fig.colorbar(im, ax=ax[0], label="power", pad=0.02)
    ax[0].set_xlabel("Azimuth (deg)"); ax[0].set_ylabel("Range (m)")
    ax[0].set_title("Range-azimuth (polar bins), frame %d" % i, fontsize=10)

    try:
        xl, yl, Z = ra.to_cartesian(grid=128)
        im2 = ax[1].imshow(Z, aspect="equal", origin="lower", cmap=cmap,
                           extent=[xl[0], xl[-1], yl[0], yl[-1]])
        fig.colorbar(im2, ax=ax[1], label="power", pad=0.02)
        ax[1].set_xlabel("Lateral X (m)"); ax[1].set_ylabel("Downrange Y (m)")
        ax[1].set_title("Cartesian (as the TI Visualizer draws it)", fontsize=10)
    except Exception as e:
        ax[1].text(0.5, 0.5, "Cartesian resample unavailable\n%r" % (e,),
                   ha="center", va="center", transform=ax[1].transAxes,
                   color="0.4")
    fig.suptitle("Beamformed from TLV %d angle I/Q -- %s"
                 % (rec.angle_tlv or 8, rec.name), y=0.99)
    fig.tight_layout()
    return _finish(fig, save_path, dpi)


def plot_all(rec, out_dir: str, dpi: int = 200):
    """Write whatever this recording supports. Returns the paths written."""
    os.makedirs(out_dir, exist_ok=True)
    out = []
    jobs = [
        ("range_noise_profile", lambda p: plot_range_noise_profile(rec, p, dpi=dpi)),
        ("processing_stats",    lambda p: plot_processing_stats(rec, p, dpi=dpi)),
        ("temperature",         lambda p: plot_temperature(rec, p, dpi=dpi)),
        ("range_azimuth_frame", lambda p: plot_range_azimuth_frame(rec, None, p, dpi=dpi)),
    ]
    for name, fn in jobs:
        p = os.path.join(out_dir, "%s.png" % name)
        try:
            r = fn(p)
            if r:
                out.append(r)
        except Exception as e:
            print("   (diagnostic figure %s failed: %r)" % (name, e))
    return out
