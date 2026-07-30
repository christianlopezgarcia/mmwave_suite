"""
live.py -- Real-time capture pipeline: EVM -> pyserial -> TLV frames -> your code.

Architecture (three decoupled stages, so a slow consumer can never cost frames):

    [reader thread]      pure I/O.  read(), timestamp, hand off.  Never parses.
         |  queue
    [parser thread]      StreamParser.feed() -> whole Frames.  Also writes the
         |  queue        raw bytes to disk *before* parsing, exactly as TI does.
    [your callback]      runs on the parser thread, or drains frame_queue from
                         your own thread/process.

This inverts the visualizer's failure mode.  In mmWave.js:271 a frame is only
queued for processing when `onPlotsTab === true && in_process1 === false`, so
the UI silently discards frames whenever it is busy or you switched tabs.  Here
the recording is byte-complete by construction and parsing back-pressure shows
up as a counter you can read, not as missing data.

Anything this pipeline *does* drop is counted and reported.  See
`CaptureStats.frames_dropped_from_queue` and `link.reader_error`.

Outputs, per run directory:
    <name>.dat        raw UART bytes, byte-identical in format to a TI recording
                      (so it still plays back in the visualizer and in your
                      existing dat_parser_plots pipeline)
    <name>.cfg        copy of the configuration actually sent
    <name>.meta.json  run metadata: ports, device version, derived geometry
    <name>.idx.jsonl  one line per frame: host timestamp, frame number, byte
                      offset into the .dat, point count.  This is the piece TI
                      never gives you and that you need for a digital twin.

Usage:
    python -m mmwave_suite.extraction.live --cli COM4 --data COM5 --cfg profile.cfg \
        --out ./runs --seconds 60
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable, List, Optional

from .cfgparse import RadarConfig, parse_cfg
from .link import ConfigError, PortError, RadarLink, find_ports, guess_evm_ports
from .tlv import Frame, StreamParser

# If no bytes at all have arrived this long after the config was accepted, the
# data port is almost certainly the wrong one (or the sensor never started).
NO_DATA_WARN_SECONDS = 3.0

# Flush the .dat this often so an abnormal exit costs at most this much.
FLUSH_INTERVAL_SECONDS = 2.0


class LiveCapture:
    def __init__(self,
                 cli_port: str,
                 data_port: str,
                 cfg_path: str,
                 out_dir: str,
                 name: Optional[str] = None,
                 cli_baud: int = 115200,
                 data_baud: int = 921600,
                 record_raw: bool = True,
                 verbose: bool = True):
        self.cfg_path = cfg_path
        self.cfg: RadarConfig = parse_cfg(cfg_path)
        self.link = RadarLink(cli_port, data_port, cli_baud, data_baud, verbose=verbose)
        self.parser = StreamParser(geometry=self.cfg.geometry())
        self.verbose = verbose
        self.record_raw = record_raw

        self.name = name or ("run_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        self.run_dir = os.path.join(out_dir, self.name)
        os.makedirs(self.run_dir, exist_ok=True)

        self.dat_path = os.path.join(self.run_dir, self.name + ".dat")
        self.idx_path = os.path.join(self.run_dir, self.name + ".idx.jsonl")
        self.meta_path = os.path.join(self.run_dir, self.name + ".meta.json")

        self._dat = None
        self._idx = None
        self._files_lock = threading.Lock()
        self._raw_q: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self._stopped = False
        self._parser_thread: Optional[threading.Thread] = None
        self._last_flush = 0.0

        self.frames_seen = 0
        self.points_seen = 0
        self.frames_dropped_from_queue = 0
        self.device_info = None
        self.t0: Optional[float] = None
        self.t_config_done: Optional[float] = None
        self.frame_queue: "queue.Queue[Frame]" = queue.Queue(maxsize=4096)

    # -- helpers -----------------------------------------------------------

    def _log(self, m: str) -> None:
        if self.verbose:
            print("[live] %s" % m)

    def _on_chunk(self, ts: float, chunk: bytes) -> None:
        """Runs on the reader thread.  Write raw bytes first, then queue.

        Writing before parsing is what makes the .dat authoritative -- the same
        ordering TI uses at mmWave.js:305, where saveStreamData(data) is the
        very first statement in the data handler.
        """
        with self._files_lock:
            if self._dat is not None:
                self._dat.write(chunk)
                now = time.perf_counter()
                if now - self._last_flush >= FLUSH_INTERVAL_SECONDS:
                    self._dat.flush()
                    if self._idx is not None:
                        self._idx.flush()
                    self._last_flush = now
        self._raw_q.put((ts, chunk))

    def _parser_loop(self, on_frame: Optional[Callable[[Frame], None]]) -> None:
        while True:
            try:
                ts, chunk = self._raw_q.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            frames = self.parser.feed(chunk, host_timestamp=ts)
            for fr in frames:
                self.frames_seen += 1
                self.points_seen += fr.num_points
                if self.t0 is None:
                    self.t0 = fr.host_timestamp
                with self._files_lock:
                    if self._idx is not None:
                        self._idx.write(json.dumps({
                            "t": round((fr.host_timestamp or 0) - (self.t0 or 0), 6),
                            "frame": fr.header.frame_number,
                            "sub": fr.header.sub_frame_number,
                            "cycles": fr.header.time_cpu_cycles,
                            "off": fr.byte_offset,
                            "n_pts": fr.num_points,
                            "tlvs": list(fr.tlv_order),
                            "anom": fr.anomalies or None,
                        }) + "\n")
                if on_frame is not None:
                    on_frame(fr)
                else:
                    try:
                        self.frame_queue.put_nowait(fr)
                    except queue.Full:
                        # Counted, never silent.  The .dat is unaffected -- this
                        # only means a display consumer fell behind.
                        self.frames_dropped_from_queue += 1

    # -- run ---------------------------------------------------------------

    def start(self, on_frame: Optional[Callable[[Frame], None]] = None) -> None:
        self._log("run directory: %s" % self.run_dir)
        self._log(self.cfg.summary())

        shutil.copyfile(self.cfg_path, os.path.join(self.run_dir, self.name + ".cfg"))

        self.link.open()
        # handshake() must run BEFORE the reader starts: if the EVM is still
        # streaming from a previous session it is stopped in there, so frames
        # produced by the OLD configuration never reach the recording.
        info = self.link.handshake()
        self.device_info = info

        if info.platform and self.cfg.platform and info.platform != self.cfg.platform:
            self._log("WARNING: device reports platform %s but the .cfg says %s"
                      % (info.platform, self.cfg.platform))
        if info.sdk_version_uint16 and self.cfg.sdk_version_uint16 \
                and info.sdk_version_uint16 != self.cfg.sdk_version_uint16:
            self._log("WARNING: device reports SDK %s but the .cfg header says %s"
                      % (info.sdk_version, self.cfg.sdk_version_str))

        if self.record_raw:
            self._dat = open(self.dat_path, "wb", buffering=1 << 20)
            self._idx = open(self.idx_path, "w", buffering=1 << 16)
            self._last_flush = time.perf_counter()

        meta = {
            "name": self.name,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "cli_port": self.link.cli_port_name,
            "data_port": self.link.data_port_name,
            "cli_baud": self.link.cli_baud,
            "data_baud": self.link.data_baud,
            "rx_buffer_enlarged": self.link.rx_buffer_ok,
            "device": {
                "platform": info.platform,
                "sdk_version": info.sdk_version,
                "device_info": info.device_info,
                "handshake_ok": bool(info.sdk_version),
            },
            "cfg_file": os.path.abspath(self.cfg_path),
            "derived": {
                "num_range_bins": self.cfg.num_range_bins,
                "num_doppler_bins": self.cfg.num_doppler_bins,
                "range_idx_to_meters": self.cfg.range_idx_to_meters,
                "doppler_resolution_mps": self.cfg.doppler_resolution_mps,
                "max_velocity_mps": self.cfg.max_velocity_mps,
                "frame_periodicity_ms": self.cfg.frame_periodicity_ms,
                "num_tx_ant": self.cfg.num_tx_ant,
                "num_rx_ant": self.cfg.num_rx_ant,
            },
            "on_chip_filters": asdict(self.cfg.filters),
            "expected_tlvs": self.cfg.gui_monitor.expected_tlvs(),
        }
        with open(self.meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        self.link.start_reader(on_chunk=self._on_chunk, use_queue=False)

        self._parser_thread = threading.Thread(
            target=self._parser_loop, args=(on_frame,),
            name="mmwave-parser", daemon=True)
        self._parser_thread.start()

        with open(self.cfg_path, "r", errors="replace") as f:
            lines = f.read().splitlines()
        self.link.send_config(lines)
        self.t_config_done = time.perf_counter()
        self._log("configuration accepted; streaming")

    def check_health(self) -> Optional[str]:
        """Cheap periodic check.  Returns a warning string, or None."""
        if self.link.reader_error is not None:
            return "reader thread stopped: %r" % (self.link.reader_error,)
        if (self.t_config_done is not None and self.link.bytes_read == 0
                and time.perf_counter() - self.t_config_done > NO_DATA_WARN_SECONDS):
            return ("no bytes on the data port %s after %.0fs. Wrong data port, "
                    "or the sensor did not start. On a dual CP2105 the data "
                    "stream is the *Standard* COM port."
                    % (self.link.data_port_name, NO_DATA_WARN_SECONDS))
        return None

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            try:
                self.link.sensor_stop()
            except BaseException as e:
                self._log("WARNING: sensorStop failed during shutdown: %r" % (e,))

            reader_done = self.link.stop_reader()
            self._stop.set()

            parser_done = True
            if self._parser_thread is not None:
                self._parser_thread.join(5.0)
                parser_done = not self._parser_thread.is_alive()
                self._parser_thread = None

            if not (reader_done and parser_done):
                # Closing the handles under a live writer would raise
                # ValueError: I/O operation on closed file inside that thread
                # and truncate the index.  Leak the handles instead; the OS
                # closes them at exit and the data on disk stays consistent.
                self._log("WARNING: worker threads did not stop cleanly "
                          "(reader_done=%s parser_done=%s); leaving files open "
                          "so nothing is truncated" % (reader_done, parser_done))
                with self._files_lock:
                    for h in (self._dat, self._idx):
                        if h is not None:
                            try:
                                h.flush()
                            except Exception:
                                pass
                return

            with self._files_lock:
                for h in (self._dat, self._idx):
                    if h is not None:
                        try:
                            h.flush()
                            h.close()
                        except Exception:
                            pass
                self._dat = self._idx = None
        finally:
            try:
                self.link.close()
            except BaseException as e:
                self._log("WARNING: closing ports failed: %r" % (e,))

    def summary(self) -> str:
        s = self.parser.stats
        dur = 0.0
        if self.t0 is not None:
            dur = max(0.0, time.perf_counter() - self.t0)
        lines = [
            "frames=%d points=%d bytes=%d duration=%.1fs (%.2f fps)"
            % (self.frames_seen, self.points_seen, self.link.bytes_read, dur,
               self.frames_seen / dur if dur else 0.0),
            "  lost_bytes=%d resyncs=%d false_magic=%d zero_point_frames=%d"
            % (s.bytes_skipped, s.resyncs, s.false_magic_rejected,
               s.frames_with_zero_points),
            "  anomalies=%s" % (s.anomaly_counts or "none"),
        ]
        if self.frames_dropped_from_queue:
            lines.append("  WARNING: %d frames dropped from the display queue "
                         "(the .dat is unaffected)" % self.frames_dropped_from_queue)
        if s.resyncs:
            lines.append("  NOTE: %d resync(s) means the host lost bytes mid-stream. "
                         "If this is non-zero, prefer mmwave_suite.extraction.live (no plots) "
                         "for captures that matter." % s.resyncs)
        if self.link.reader_error is not None:
            lines.append("  ERROR: reader stopped early: %r" % (self.link.reader_error,))
        return "\n".join(lines)


# ---------------------------------------------------------------------------


def resolve_ports(cli: Optional[str], data: Optional[str]
                  ) -> "tuple[Optional[str], Optional[str]]":
    if cli and data:
        return cli, data
    gc, gd = guess_evm_ports()
    return cli or gc, data or gd


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cli", help="CLI/command COM port (e.g. COM4)")
    ap.add_argument("--data", help="data COM port (e.g. COM5)")
    ap.add_argument("--cfg", required=False, help="path to .cfg")
    ap.add_argument("--out", default="./runs", help="output root directory")
    ap.add_argument("--name", help="run name (default: timestamp)")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="stop after N seconds (0 = until Ctrl-C)")
    ap.add_argument("--cli-baud", type=int, default=115200)
    ap.add_argument("--data-baud", type=int, default=921600)
    ap.add_argument("--list-ports", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    if a.list_ports:
        print("Serial ports:")
        find_ports()
        cli, data = guess_evm_ports()
        print("\nBest guess: CLI=%s DATA=%s" % (cli, data))
        if not (cli and data):
            print("Could not identify both ports -- pass --cli and --data "
                  "explicitly.\nOn a Silicon Labs dual CP2105 the CLI is the "
                  "'Enhanced COM Port' and the data stream is the 'Standard "
                  "COM Port'.")
        return 0

    if not a.cfg:
        ap.error("--cfg is required unless --list-ports is given")
    cli, data = resolve_ports(a.cli, a.data)
    if not cli or not data:
        print("Could not determine both ports (CLI=%s DATA=%s).\n"
              "Run with --list-ports, then pass --cli and --data explicitly."
              % (cli, data), file=sys.stderr)
        return 2

    cap = LiveCapture(cli, data, a.cfg, a.out, name=a.name,
                      cli_baud=a.cli_baud, data_baud=a.data_baud,
                      verbose=not a.quiet)

    last_print = [0.0]

    def on_frame(fr: Frame) -> None:
        now = time.perf_counter()
        if now - last_print[0] >= 1.0:
            last_print[0] = now
            snr = fr.snr_db
            extra = ""
            if snr is not None and snr.size:
                extra = "  snr %.1f..%.1f dB" % (float(snr.min()), float(snr.max()))
            print("  frame %-8d pts %-4d tlvs %-14s%s"
                  % (fr.header.frame_number, fr.num_points,
                     ",".join(map(str, fr.tlv_order)), extra))

    try:
        cap.start(on_frame=on_frame)
    except (ConfigError, PortError) as e:
        print("\nSetup failed: %s" % e, file=sys.stderr)
        cap.stop()
        return 3

    warned = False
    try:
        deadline = time.perf_counter() + a.seconds if a.seconds > 0 else None
        while deadline is None or time.perf_counter() < deadline:
            time.sleep(0.25)
            if not warned:
                w = cap.check_health()
                if w:
                    warned = True
                    print("\n[live] WARNING: %s\n" % w, file=sys.stderr)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        try:
            cap.stop()
        except KeyboardInterrupt:
            # A second Ctrl-C during teardown must not abort the rest of it.
            cap.stop()

    print("\n" + cap.summary())
    print("\nWrote:\n  %s\n  %s\n  %s"
          % (cap.dat_path, cap.idx_path, cap.meta_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
