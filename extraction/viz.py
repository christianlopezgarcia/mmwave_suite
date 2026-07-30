"""
viz.py -- Replica of the TI mmWave Demo Visualizer plot panel, in matplotlib.

Reproduces the five plots the Visualizer draws, from the same data, with the
same axes, scaling and accumulation behaviour.  Works from a live EVM or from
a recorded .dat.

Plot layout follows `setupPlots` (mmWave.js:3719-4076); which plots appear is
driven by `guiMonitor` exactly as in the app:

  plot1  Detected objects.  3D scatter for AOP / elevation-capable parts
         (mmWave.js:3760-3797), otherwise 2D X-Y with a polar grid overlay
         (mmWave.js:3798-3876).
  plot2  Range profile for zero Doppler, plus noise profile and the range-
         profile value at each detected point (mmWave.js:3883-3920).
  plot3  Doppler-Range heat map if enabled, else a Doppler-Range scatter of the
         current frame (mmWave.js:3921-3990).
  plot4  Range-Azimuth heat map, FFT + griddata onto a 100x100 Cartesian grid
         (mmWave.js:2933-3014 for TLV 4, 3017-3282 for the AOP TLV 8).
  plot5  Active / interframe CPU load, rolling 100 frames (mmWave.js:4037-4070).

IMPORTANT -- accumulation.  The Visualizer's plot1 does NOT show one frame.  It
shows a sliding window of `round(display_seconds * 1000 / framePeriodicity)`
frames of superimposed points (mmWave.js:4383-4406, 2689-2706).  That is
reproduced here and defaults to matching TI.  Use `--accumulate 0` to see one
frame at a time -- the honest per-frame view, and the one you want when judging
whether a point is a ghost.

Usage
-----
  # replay a recording, TI-style
  python -m mmwave_suite.extraction.viz --dat capture.dat --cfg capture.cfg

  # replay as fast as possible, one frame per update, no accumulation
  python -m mmwave_suite.extraction.viz --dat capture.dat --cfg capture.cfg --fast --accumulate 0

  # live from the EVM (also records a .dat)
  python -m mmwave_suite.extraction.viz --cli COM4 --data COM5 --cfg profile.cfg --out ./runs
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque
from typing import List, Optional

import numpy as np

from .cfgparse import RadarConfig, parse_cfg
from .tlv import Frame, parse_dat

# mmWave.js:1800-1804 -- app defaults
NUM_ANGLE_BINS = 64
DEFAULT_RANGE_DEPTH = 10.0
DEFAULT_RANGE_WIDTH = 5.0
DEFAULT_MAX_RANGE_PROFILE_Y = 2e6


# ---------------------------------------------------------------------------
# Range-azimuth heat map
# ---------------------------------------------------------------------------

# mmWave.js:3066-3087 -- which virtual-antenna symbols feed the AOP heat map.
# Keyed by (numTxAnt, tuple(sorted(enabled tx))).
_AOP_SYMBOLS = {
    (1, (0,)):     [(0, 3), (0, 1)],
    (2, (0, 1)):   [(1, 3), (1, 1)],
    (2, (1, 2)):   [(2, 3), (2, 1), (1, 3), (1, 1)],
    (2, (0, 2)):   [(2, 3), (2, 1)],
    (3, (0, 1, 2)): [(2, 3), (2, 1), (1, 3), (1, 1)],
}


def _angle_fft(sym: np.ndarray) -> np.ndarray:
    """(range_bins, n_sym) complex -> (range_bins, NUM_ANGLE_BINS-1) magnitude.

    Zero-pad to NUM_ANGLE_BINS, FFT along the antenna axis, magnitude,
    fftshift, then drop the first column and reverse -- mmWave.js:2967-2978.
    """
    n_rng, n_sym = sym.shape
    padded = np.zeros((n_rng, NUM_ANGLE_BINS), dtype=np.complex128)
    padded[:, :n_sym] = sym
    Q = np.abs(np.fft.fft(padded, n=NUM_ANGLE_BINS, axis=1))
    Q = np.roll(Q, NUM_ANGLE_BINS // 2, axis=1)          # fftshift
    return Q[:, 1:][:, ::-1]                              # drop col 0, fliplr


def azimuth_symbols(frame: Frame, cfg: RadarConfig) -> Optional[np.ndarray]:
    """Extract the complex symbols used for the range-azimuth heat map."""
    if frame.azimuth_heatmap is not None:
        c = frame.azimuth_heatmap
        if c.ndim == 1:
            n_ant = cfg.num_tx_azim_ant * cfg.num_rx_ant
            if not n_ant or c.size % n_ant:
                return None
            c = c.reshape(-1, n_ant)
        return c

    if frame.azimuth_elev_heatmap is not None:
        # AOP (TLV 8): pick specific (tx, rx) symbols by transmission order.
        c = frame.azimuth_elev_heatmap
        n_ant = cfg.num_virtual_ant
        if c.ndim == 1:
            if not n_ant or c.size % n_ant:
                return None
            c = c.reshape(-1, n_ant)
        enabled = tuple(sorted(t for t, s in cfg.tx_chirp_index.items() if s >= 0))
        key = (cfg.num_tx_ant, enabled)
        if key not in _AOP_SYMBOLS:
            return None
        idx = [cfg.tx_chirp_index[tx] * cfg.num_rx_ant + rx
               for tx, rx in _AOP_SYMBOLS[key]]          # mmWave.js:3215-3216
        if max(idx) >= c.shape[1]:
            return None
        return c[:, idx]

    return None


class RangeAzimuthGrid:
    """Cached polar->Cartesian griddata, mirroring TI's griddata_from_cache."""

    def __init__(self, cfg: RadarConfig, range_width: float, range_depth: float,
                 points: int = 100):
        from scipy.spatial import Delaunay

        # mmWave.js:3251 -- theta = asin([-31..31] * 2/64)
        k = np.arange(-NUM_ANGLE_BINS // 2 + 1, NUM_ANGLE_BINS // 2)
        theta = np.arcsin(k * (2.0 / NUM_ANGLE_BINS))
        # mmWave.js:3252-3256 -- range bias subtracted, then clamped at 0.
        rng = cfg.range_axis_m()

        posX = np.outer(rng, np.sin(theta))
        posY = np.outer(rng, np.cos(theta))

        self.xlin = np.linspace(-range_width, range_width, points)
        self.ylin = np.linspace(0.0, range_depth, points)
        self.XI, self.YI = np.meshgrid(self.xlin, self.ylin)
        self._tri = Delaunay(np.column_stack([posX.ravel(), posY.ravel()]))
        self.shape = (cfg.num_range_bins, len(theta))

    def __call__(self, values: np.ndarray) -> np.ndarray:
        from scipy.interpolate import LinearNDInterpolator
        interp = LinearNDInterpolator(self._tri, values.ravel(), fill_value=np.nan)
        return interp(self.XI, self.YI)


# ---------------------------------------------------------------------------
# The plot panel
# ---------------------------------------------------------------------------


class Visualizer:
    def __init__(self,
                 cfg: RadarConfig,
                 range_width: float = DEFAULT_RANGE_WIDTH,
                 range_depth: float = DEFAULT_RANGE_DEPTH,
                 accumulate_seconds: float = 0.0,
                 log_scale: bool = True,
                 max_range_profile_y: float = DEFAULT_MAX_RANGE_PROFILE_Y,
                 title_suffix: str = ""):
        import matplotlib.pyplot as plt

        self.cfg = cfg
        self.range_width = range_width
        self.range_depth = range_depth
        self.log_scale = log_scale
        # mmWave.js:3753 takes Math.abs() of the rpymax textbox.  Guard <= 0 too:
        # np.log2(0) is -inf and set_ylim would raise.
        self.max_range_profile_y = max(abs(float(max_range_profile_y)), 1.0)
        self.plt = plt

        # mmWave.js:4387 -- aggFrames = round(seconds*1000 / framePeriodicity)
        per = cfg.frame_periodicity_ms or 100.0
        self.agg_frames = max(1, int(round(accumulate_seconds * 1000.0 / per)))
        self.accumulating = self.agg_frames > 1
        self._acc: deque = deque(maxlen=self.agg_frames)

        g = cfg.gui_monitor
        self.show_scatter = g.detected_objects in (1, 2)
        self.show_rp = bool(g.log_mag_range or g.noise_profile)
        self.show_rd_heat = bool(g.range_doppler_heatmap)
        self.show_rd_scatter = self.show_scatter and not self.show_rd_heat
        self.show_az = bool(g.range_azimuth_heatmap)
        self.show_stats = bool(g.stats_info)

        panels = [p for p, on in [
            ("scatter", self.show_scatter),
            ("rp", self.show_rp),
            ("rd", self.show_rd_heat or self.show_rd_scatter),
            ("az", self.show_az),
            ("cpu", self.show_stats)] if on]
        n = max(1, len(panels))
        cols = 2 if n > 1 else 1
        rows = (n + cols - 1) // cols

        self.fig = plt.figure(figsize=(7 * cols, 5 * rows))
        self.fig.canvas.manager.set_window_title(
            "mmwave_direct visualizer" + (" - " + title_suffix if title_suffix else ""))
        self.ax = {}
        self.art = {}

        # AOP and elevation-capable parts get a 3D scatter (mmWave.js:3760-3763)
        self.use_3d = (cfg.platform.endswith("_AOP") or cfg.num_tx_elev_ant == 1)

        for i, p in enumerate(panels):
            if p == "scatter" and self.use_3d:
                ax = self.fig.add_subplot(rows, cols, i + 1, projection="3d")
            else:
                ax = self.fig.add_subplot(rows, cols, i + 1)
            self.ax[p] = ax

        self._init_scatter()
        self._init_rp()
        self._init_rd()
        self._init_az()
        self._init_cpu()

        self.fig.tight_layout()
        self.fig.subplots_adjust(top=0.90)   # leave room for the status suptitle
        self._cpu_active = deque([0] * 100, maxlen=100)
        self._cpu_inter = deque([0] * 100, maxlen=100)
        self._az_grid: Optional[RangeAzimuthGrid] = None
        self.frames_drawn = 0

    # -- panel setup -------------------------------------------------------

    def _init_scatter(self):
        if not self.show_scatter:
            return
        ax = self.ax["scatter"]
        rw, rd = self.range_width, self.range_depth
        if self.use_3d:
            # mmWave.js:3776-3797
            ax.set_xlim(-rw, rw); ax.set_ylim(0, rd); ax.set_zlim(-rw, rw)
            ax.set_xlabel("X in meters"); ax.set_ylabel("Y in meters")
            ax.set_zlabel("Z")
            # TI camera eye:(1.5,1.5,0.1) center:(0,0,-0.3) up:+z
            # (mmWave.js:3792-3796) -> azimuth 45 deg, ~10.7 deg above the
            # focal point.  azim=45 puts +x toward screen LEFT as TI does;
            # the previous azim=-125 mirrored the scene left-to-right.
            ax.view_init(elev=11, azim=45)
            self.art["scatter"] = ax.scatter([], [], [], s=9, c="tab:cyan",
                                             depthshade=False)
        else:
            # mmWave.js:3798-3876 -- dark background, polar grid overlay
            ax.set_facecolor((0.0, 0.0, 0.376))       # rgb(0,0,96)
            radii = [(i + 1) * rd / 4 for i in range(4)]
            w = np.linspace(np.pi / 6, 5 * np.pi / 6, 16)
            for r in radii:
                ax.plot(r * np.cos(w), r * np.sin(w), color="0.5", lw=1, zorder=1)
            for i in range(5):
                a = np.pi / 6 + i * np.pi * 2 / 12
                ax.plot([0, rd * np.cos(a)], [0, rd * np.sin(a)],
                        color="0.5", lw=1, zorder=1)
            ax.set_xlim(-rw, rw); ax.set_ylim(0, radii[-1])
            ax.set_xlabel("Distance along lateral axis (meters)")
            ax.set_ylabel("Distance along longitudinal axis (meters)")
            self.art["scatter"] = ax.scatter([], [], s=16, c="lime", zorder=3)
        ax.set_title("3D Scatter Plot" if self.use_3d else "X-Y Scatter Plot")

    def _init_rp(self):
        if not self.show_rp:
            return
        ax = self.ax["rp"]
        cfg = self.cfg
        xmax = cfg.range_idx_to_meters * cfg.num_range_bins
        ax.set_xlim(0, xmax)
        # mmWave.js:3900-3913
        ymax = (np.log2(self.max_range_profile_y) * cfg.to_db
                if self.log_scale else self.max_range_profile_y)
        ax.set_ylim(0, ymax)
        ax.set_xlabel("Range (meters)")
        ax.set_ylabel("Relative Power (dB)" if self.log_scale
                      else "Relative Power (linear)")
        ax.set_title("Range Profile for zero Doppler")
        ax.grid(alpha=0.25)
        (self.art["rp"],) = ax.plot([], [], color="blue", lw=1, label="Range Profile")
        (self.art["rp_det"],) = ax.plot([], [], "o", ms=5, color="tab:orange",
                                        ls="none", label="Detected Points")
        (self.art["np"],) = ax.plot([], [], color="tab:green", lw=1,
                                    label="Noise Profile")
        ax.legend(loc="upper right", fontsize=8)

    def _init_rd(self):
        if "rd" not in self.ax:
            return
        ax = self.ax["rd"]
        cfg = self.cfg
        if self.show_rd_heat:
            # mmWave.js:3936 -- the axis RANGE is res*(N/2 - 1) ...
            res = max(cfg.doppler_resolution_mps, 1e-6)
            rim = cfg.range_idx_to_meters
            dr = res * (cfg.num_doppler_bins / 2 - 1)
            # ... but the DATA spans -N/2*res .. (N/2-1)*res (mmWave.js:3315-3319).
            # imshow's extent describes pixel EDGES, so it must be half a bin
            # outside those sample centres; passing the axis range instead
            # compresses the velocity scale by (N-2)/N and shifts zero Doppler
            # by half a bin.  Same half-bin logic on the range axis.
            self.art["rd"] = ax.imshow(
                np.zeros((cfg.num_doppler_bins, cfg.num_range_bins)),
                origin="lower", aspect="auto", cmap="jet",
                extent=[-0.5 * rim - cfg.range_bias_m,
                        (cfg.num_range_bins - 0.5) * rim - cfg.range_bias_m,
                        -(cfg.num_doppler_bins / 2 + 0.5) * res,
                        (cfg.num_doppler_bins / 2 - 0.5) * res],
                interpolation="bilinear")
            ax.set_xlim(0, self.range_depth); ax.set_ylim(-dr, dr)
            ax.set_title("Doppler-Range Heatmap")
        else:
            # mmWave.js:3954-3990 -- current frame only, NOT accumulated.
            # getMaxDopplerRange (mmWave.js:3502-3527) doubles the axis when
            # velocity disambiguation is on; the heatmap branch above does not.
            self.art["rd"] = ax.scatter([], [], s=16, c="lime")
            ax.set_facecolor((0.0, 0.0, 0.376))
            ax.set_xlim(0, self.range_depth)
            dr = cfg.max_velocity_mps * (2.0 if cfg.filters.extended_max_velocity else 1.0)
            dr = max(dr, 1e-3)
            ax.set_ylim(-dr, dr)
            ax.set_title("Doppler-Range Plot")
            ax.grid(alpha=0.25)
        ax.set_xlabel("Range (meters)"); ax.set_ylabel("Doppler (m/s)")

    def _init_az(self):
        if not self.show_az:
            return
        ax = self.ax["az"]
        self.art["az"] = ax.imshow(
            np.zeros((100, 100)), origin="lower", aspect="equal", cmap="jet",
            extent=[-self.range_width, self.range_width, 0, self.range_depth],
            interpolation="bilinear")
        ax.set_xlabel("Distance along lateral axis (meters)")
        ax.set_ylabel("Distance along longitudinal axis (meters)")
        ax.set_title("Range-Azimuth Heatmap")

    def _init_cpu(self):
        if not self.show_stats:
            return
        ax = self.ax["cpu"]
        ax.set_xlim(0, 100); ax.set_ylim(0, 100)
        ax.set_xlabel("Frames"); ax.set_ylabel("% CPU Load")
        ax.set_title("Active and Interframe CPU Load")
        ax.grid(alpha=0.25)
        (self.art["cpu_a"],) = ax.plot([], [], label="Active frame")
        (self.art["cpu_i"],) = ax.plot([], [], label="Interframe")
        ax.legend(loc="upper right", fontsize=8)

    # -- per-frame update --------------------------------------------------

    def update(self, f: Frame) -> None:
        cfg = self.cfg

        # ---- plot1: detected objects -------------------------------------
        if self.show_scatter:
            pts = f.points
            if pts is not None and pts.size:
                self._acc.append((pts["x"].copy(), pts["y"].copy(), pts["z"].copy()))
            else:
                self._acc.append((np.empty(0), np.empty(0), np.empty(0)))
            xs = np.concatenate([a[0] for a in self._acc]) if self._acc else np.empty(0)
            ys = np.concatenate([a[1] for a in self._acc]) if self._acc else np.empty(0)
            zs = np.concatenate([a[2] for a in self._acc]) if self._acc else np.empty(0)
            if self.use_3d:
                self.art["scatter"]._offsets3d = (xs, ys, zs)
            else:
                self.art["scatter"].set_offsets(
                    np.column_stack([xs, ys]) if xs.size else np.empty((0, 2)))

        # ---- plot2: range / noise profile --------------------------------
        if self.show_rp:
            # mmWave.js:2874-2878 -- range bias subtracted, clamped at 0.  The
            # detected-point markers index this same axis, exactly as TI does.
            x = cfg.range_axis_m()
            if f.range_profile is not None and f.range_profile.size:
                y = (cfg.range_profile_db(f.range_profile) if self.log_scale
                     else cfg.range_profile_linear(f.range_profile))
                n = min(len(x), len(y))
                self.art["rp"].set_data(x[:n], y[:n])
                # mmWave.js:2908-2922 -- mark detected points at zero Doppler
                if f.points is not None and f.points.size and cfg.range_idx_to_meters:
                    rng = f.range_m
                    dop = f.points["doppler"]
                    ridx = np.round(rng / cfg.range_idx_to_meters).astype(int)
                    didx = np.round(dop / cfg.doppler_resolution_mps).astype(int) \
                        if cfg.doppler_resolution_mps else np.zeros_like(ridx)
                    sel = (didx == 0) & (ridx >= 0) & (ridx < n)
                    self.art["rp_det"].set_data(x[ridx[sel]], y[ridx[sel]])
                else:
                    self.art["rp_det"].set_data([], [])
            if f.noise_profile is not None and f.noise_profile.size:
                yn = (cfg.range_profile_db(f.noise_profile) if self.log_scale
                      else cfg.range_profile_linear(f.noise_profile))
                n = min(len(x), len(yn))
                self.art["np"].set_data(x[:n], yn[:n])

        # ---- plot3: doppler-range ----------------------------------------
        if self.show_rd_heat and f.range_doppler_heatmap is not None:
            z = f.range_doppler_heatmap
            if z.ndim == 2:
                # mmWave.js:3308 -- fftshift along the Doppler axis
                z = np.roll(z, z.shape[0] // 2, axis=0)
                self.art["rd"].set_data(z)
                self.art["rd"].set_clim(float(z.min()), float(z.max()))
        elif self.show_rd_scatter:
            if f.points is not None and f.points.size:
                self.art["rd"].set_offsets(
                    np.column_stack([f.range_m, f.points["doppler"]]))
            else:
                self.art["rd"].set_offsets(np.empty((0, 2)))

        # ---- plot4: range-azimuth heatmap --------------------------------
        if self.show_az:
            sym = azimuth_symbols(f, cfg)
            if sym is not None and sym.size:
                if self._az_grid is None:
                    self._az_grid = RangeAzimuthGrid(
                        cfg, self.range_width, self.range_depth)
                mag = _angle_fft(sym)
                if mag.shape == self._az_grid.shape:
                    zi = self._az_grid(mag)
                    self.art["az"].set_data(zi)
                    finite = zi[np.isfinite(zi)]
                    if finite.size:
                        self.art["az"].set_clim(float(finite.min()), float(finite.max()))

        # ---- plot5: CPU load ---------------------------------------------
        if self.show_stats and f.stats:
            self._cpu_active.append(f.stats["active_frame_cpu_load_pct"])
            self._cpu_inter.append(f.stats["inter_frame_cpu_load_pct"])
            xr = np.arange(len(self._cpu_active))
            self.art["cpu_a"].set_data(xr, np.array(self._cpu_active))
            self.art["cpu_i"].set_data(xr, np.array(self._cpu_inter))

        self.frames_drawn += 1
        mode = ("accumulating %d frames (%.1fs) -- TI default"
                % (self.agg_frames, self.agg_frames * self.cfg.frame_periodicity_ms / 1000.0)
                ) if self.accumulating else "single frame"
        self.fig.suptitle(
            "frame %d   |   %d points this frame   |   %s"
            % (f.header.frame_number, f.num_points, mode), fontsize=10, y=0.995)

    def show(self):
        self.plt.show()

    def pause(self, seconds: float):
        self.plt.pause(max(seconds, 1e-3))

    def is_open(self) -> bool:
        return bool(self.plt.get_fignums())


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def replay(dat_path: str, cfg_path: str, **kw) -> int:
    cfg = parse_cfg(cfg_path)
    fast = kw.pop("fast", False)
    frames, stats = parse_dat(dat_path, geometry=cfg.geometry())
    print("Loaded %d frames from %s" % (len(frames), os.path.basename(dat_path)))
    print("  zero-detection frames: %d   skipped bytes: %d   resyncs: %d"
          % (stats.frames_with_zero_points, stats.bytes_skipped, stats.resyncs))
    if not frames:
        print("No frames parsed.", file=sys.stderr)
        return 1

    # Cross-check the .cfg against the stream, the way audit.py does.  A
    # mismatched .cfg mis-paces replay and mis-scales every axis, silently.
    observed = next((int(f.range_profile.size) for f in frames
                     if f.range_profile is not None), None)
    if observed is not None and observed != cfg.num_range_bins:
        print("  WARNING: this .cfg derives %d range bins but the stream "
              "carries %d -- the .cfg does not match this recording. Range and "
              "Doppler axes will be wrong."
              % (cfg.num_range_bins, observed), file=sys.stderr)
    sdk = frames[0].header.sdk_version_uint16
    if cfg.sdk_version_uint16 and sdk and sdk != cfg.sdk_version_uint16:
        print("  WARNING: stream reports SDK %s, .cfg header says %s"
              % (frames[0].header.sdk_version_str, cfg.sdk_version_str),
              file=sys.stderr)

    v = Visualizer(cfg, title_suffix=os.path.basename(dat_path), **kw)
    period = (cfg.frame_periodicity_ms or 100.0) / 1000.0
    for f in frames:
        if not v.is_open():
            break
        t = time.perf_counter()
        v.update(f)
        v.pause(0.001 if fast else max(0.001, period - (time.perf_counter() - t)))
    print("Displayed %d frames. Close the window to exit." % v.frames_drawn)
    if v.is_open():
        v.show()
    return 0


def live(cli_port: str, data_port: str, cfg_path: str, out_dir: Optional[str],
         **kw) -> int:
    import queue
    from .link import ConfigError, PortError
    from .live import LiveCapture

    cfg = parse_cfg(cfg_path)
    v = Visualizer(cfg, title_suffix="LIVE", **kw)

    cap = LiveCapture(cli_port, data_port, cfg_path, out_dir or "./runs",
                      record_raw=bool(out_dir), verbose=True)

    # start() must be INSIDE the try: a failure part-way through leaves the
    # reader thread running, the .dat unflushed and both COM ports held.
    try:
        cap.start()      # frames land in cap.frame_queue
        print("Streaming. Close the plot window to stop.")
        warned = False
        while v.is_open():
            drained = 0
            try:
                # Drain generously; the display falling behind must not cost
                # frames that the recording already has.
                while drained < 64:
                    f = cap.frame_queue.get_nowait()
                    v.update(f)
                    drained += 1
            except queue.Empty:
                pass
            if not warned:
                w = cap.check_health()
                if w:
                    warned = True
                    print("\n[viz] WARNING: %s\n" % w, file=sys.stderr)
            v.pause(0.02)
    except (ConfigError, PortError) as e:
        print("\nSetup failed: %s" % e, file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        pass
    finally:
        try:
            cap.stop()
        except KeyboardInterrupt:
            cap.stop()
        print("\n" + cap.summary())
        if out_dir:
            print("Recording: %s" % cap.dat_path)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dat", help="replay this recording")
    ap.add_argument("--cfg", required=True, help="the .cfg used for the capture")
    ap.add_argument("--cli", help="live: CLI/command COM port")
    ap.add_argument("--data", help="live: data COM port")
    ap.add_argument("--out", help="live: record into this directory")
    ap.add_argument("--fast", action="store_true",
                    help="replay as fast as possible instead of at frame rate")
    ap.add_argument("--accumulate", type=float, default=0.0, metavar="SECONDS",
                    help="point-cloud persistence in the scatter plot. 0 = one "
                         "frame at a time (default, honest). TI's slider "
                         "defaults to a multi-second window.")
    ap.add_argument("--range-width", type=float, default=DEFAULT_RANGE_WIDTH)
    ap.add_argument("--range-depth", type=float, default=DEFAULT_RANGE_DEPTH)
    ap.add_argument("--linear", action="store_true",
                    help="range profile on a linear axis instead of dB")
    ap.add_argument("--rp-ymax", type=float, default=DEFAULT_MAX_RANGE_PROFILE_Y,
                    metavar="LINEAR",
                    help="range-profile y-axis maximum, in TI's linear units "
                         "(the rpymax textbox, mmWave.js:3752). In dB mode the "
                         "axis becomes log2(LINEAR)*toDB; the 2e6 default gives "
                         "126 dB.")
    a = ap.parse_args(argv)

    kw = dict(range_width=a.range_width, range_depth=a.range_depth,
              accumulate_seconds=a.accumulate, log_scale=not a.linear,
              max_range_profile_y=a.rp_ymax)

    if a.dat:
        return replay(a.dat, a.cfg, fast=a.fast, **kw)
    if a.cli or a.data:
        from .live import resolve_ports
        cli, data = resolve_ports(a.cli, a.data)
        if not cli or not data:
            print("Could not determine both ports (CLI=%s DATA=%s).\n"
                  "Run `python -m mmwave_direct.live --list-ports`, then pass "
                  "--cli and --data explicitly." % (cli, data), file=sys.stderr)
            return 2
        return live(cli, data, a.cfg, a.out, **kw)
    ap.error("give either --dat FILE, or both --cli and --data for a live session")


if __name__ == "__main__":
    raise SystemExit(main())
