"""Find the highest data-port baud rate this EVM and host actually sustain.

This is the single most consequential unknown for host-side gesture work.
Every capture in this project so far has run at 921600 baud = 92160 B/s, and
at that rate a range-Doppler heat map costs more than the link can carry: 32
range x 128 Doppler x 2 bytes is 8192 B/frame, so 10 fps and no more, when a
gesture needs 25-30.  That single number is what forces the point-cloud
route.

But 921600 is a default, not a limit.  The out-of-box demo advertises
MMWDEMO_DATAUART_MAX_BAUDRATE_SUPPORTED = 3125000 (extraction/link.py:53) and
accepts `configDataPort <baud> <ack>` to move there; `RadarLink.handshake`
already negotiates it for any `data_baud` you ask for.  At 3.125 Mbaud the
same heat map fits at 34 fps and the whole TLV-5 pipeline becomes live-capable.

Whether it WORKS is a property of the USB-serial bridge on your particular
EVM and of the host driver, not of the firmware, so it has to be measured.
The failure mode is not an error -- it is silent byte loss, which shows up
as StreamParser resyncs and skipped bytes.  This measures exactly that.

    python uart_probe.py --cfg ../cfg/xwr68xx_AOP_gesture_rdmap_8fps.cfg \
        --seconds 12 --bauds 921600 1250000 2000000 3125000

Use a config whose payload is large: probing with a point cloud proves
nothing, because 900 B/frame fits at any of these rates.  The rdmap config is
the right stress test.

Read the verdict, not the throughput.  A rate that delivers more bytes per
second while dropping some of them is worse than a slower rate that drops
none -- a resync means the parser threw away everything between two frames.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List, Optional, Sequence

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "..", "..")))

from mmwave_suite.extraction.cfgparse import parse_cfg          # noqa: E402
from mmwave_suite.extraction.link import ConfigError, PortError  # noqa: E402
from mmwave_suite.extraction.live import LiveCapture, resolve_ports  # noqa: E402

DEFAULT_BAUDS = (921600, 1250000, 2000000, 3125000)


def probe(cfg_path: str, baud: int, seconds: float, cli: str, data: str,
          out_dir: str) -> dict:
    cap = LiveCapture(cli, data, cfg_path, out_dir,
                      name="uartprobe_%d" % baud,
                      data_baud=baud, record_raw=False, verbose=False)
    r = {"baud": baud, "ok": False, "error": None}
    t0 = time.perf_counter()
    try:
        cap.start()
        # Frames land in cap.frame_queue with no consumer; drain it so a full
        # queue cannot be mistaken for a link problem.
        deadline = t0 + seconds
        while time.perf_counter() < deadline:
            time.sleep(0.05)
            while not cap.frame_queue.empty():
                cap.frame_queue.get_nowait()
    except (ConfigError, PortError) as e:
        r["error"] = str(e)
        cap.stop()
        return r
    finally:
        try:
            cap.stop()
        except Exception:
            pass

    dur = max(1e-6, time.perf_counter() - t0)
    s = cap.parser.stats
    r.update(
        seconds=dur,
        frames=cap.frames_seen,
        fps=cap.frames_seen / dur,
        bytes_read=cap.link.bytes_read,
        bytes_per_s=cap.link.bytes_read / dur,
        utilisation=(cap.link.bytes_read / dur) / (baud / 10.0),
        resyncs=s.resyncs,
        bytes_skipped=s.bytes_skipped,
        false_magic=s.false_magic_rejected,
        anomalies=dict(s.anomaly_counts),
        dropped_from_queue=cap.frames_dropped_from_queue,
        reader_error=repr(cap.link.reader_error) if cap.link.reader_error else None,
    )
    r["ok"] = (s.resyncs == 0 and s.bytes_skipped == 0
               and cap.link.reader_error is None and cap.frames_seen > 0)
    return r


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cfg", required=True,
                    help="use a HEAVY config -- the rdmap one, not points")
    ap.add_argument("--cli")
    ap.add_argument("--data")
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--bauds", type=int, nargs="+", default=list(DEFAULT_BAUDS))
    ap.add_argument("--out", default="./runs/_uart_probe")
    a = ap.parse_args(argv)

    cli, data = resolve_ports(a.cli, a.data)
    if not cli or not data:
        print("Could not determine both ports. Run\n"
              "  python -m mmwave_suite.extraction.live --list-ports",
              file=sys.stderr)
        return 2

    cfg = parse_cfg(a.cfg)
    expect_fps = (1000.0 / cfg.frame_periodicity_ms
                  if cfg.frame_periodicity_ms else 0.0)
    print("config: %s" % os.path.basename(a.cfg))
    print("  expected %.1f fps, %d range bins x %d Doppler bins"
          % (expect_fps, cfg.num_range_bins, cfg.num_doppler_bins))
    print("  TLVs requested: %s" % (cfg.gui_monitor.expected_tlvs(),))
    print("  ports: CLI=%s DATA=%s\n" % (cli, data))

    results: List[dict] = []
    for baud in a.bauds:
        print("probing %d baud for %.0f s ..." % (baud, a.seconds), flush=True)
        r = probe(a.cfg, baud, a.seconds, cli, data, a.out)
        results.append(r)
        if r["error"]:
            print("  refused: %s\n" % r["error"])
            continue
        print("  %.1f fps (%.0f%% of expected), %.0f kB/s, %.0f%% of the line"
              % (r["fps"], 100 * r["fps"] / expect_fps if expect_fps else 0,
                 r["bytes_per_s"] / 1000.0, 100 * r["utilisation"]))
        print("  resyncs %d, lost bytes %d, false magic %d%s"
              % (r["resyncs"], r["bytes_skipped"], r["false_magic"],
                 "" if r["ok"] else "   <-- NOT CLEAN"))
        if r["anomalies"]:
            print("  anomalies: %s" % r["anomalies"])
        print()

    clean = [r for r in results if r.get("ok")]
    print("=" * 66)
    if not clean:
        print("No rate was clean. If even 921600 loses bytes, the problem is")
        print("upstream of baud rate -- check the USB cable and the host's")
        print("serial buffer (link.py enlarges it to 1 MB on Windows).")
        return 1
    best = max(clean, key=lambda r: r["baud"])
    print("Highest clean rate: %d baud (%.0f kB/s usable, %.1f fps on this cfg)"
          % (best["baud"], best["baud"] / 10.0 / 1000.0, best["fps"]))
    if best["baud"] > 921600:
        gain = best["baud"] / 921600.0
        print("That is %.1fx the default. A range-Doppler heat map that ran at"
              % gain)
        print("%.1f fps now runs at %.1f fps, which changes the answer in the"
              % (10.1, 10.1 * gain))
        print("README: the TLV-5 route becomes viable for live gestures.")
        print("Set data_baud=%d in LiveCapture / --data-baud on the CLI."
              % best["baud"])
    else:
        print("No improvement over the default. The point-cloud route (mode=")
        print("\"points\") stays the only live option on stock firmware.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
