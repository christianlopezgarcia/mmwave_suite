"""The frame-callback contract shared by every application, plus two drivers.

The two drivers exist so that `develop offline, deploy live` is not a porting
exercise:

    run_offline(app, "runs/run_x/run_x.dat", "runs/run_x/run_x.cfg")
    run_live(app, cli="COM4", data="COM5", cfg="....cfg", out="./runs")

Both hand the same `AppModule` the same `Frame` objects in the same order, so
a result you reproduce from a recording is the result the sensor would have
produced.  `run_live` records the raw stream while it runs, so every live
session is replayable afterwards by definition.

Why a callback and not a queue: `LiveCapture` already gives you both
(`on_frame=` runs on the parser thread, otherwise frames land in
`cap.frame_queue`).  An application is cheap -- the gesture pipeline is a
90-element dot product per frame -- so running it on the parser thread keeps
latency at one frame and avoids a second copy.  If an application ever gets
expensive enough to threaten the reader, move it to `frame_queue`; the .dat
is written before parsing either way, so the recording cannot be damaged by a
slow consumer.  See `extraction/live.py` for that ordering.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Iterator, Optional

from ...extraction.cfgparse import parse_cfg
from ...extraction.tlv import Frame, iter_frames


class AppModule:
    """Stateful per-frame consumer.

    Subclasses override `on_frame`.  They may keep as much history as they
    like; `reset()` must return the object to its frame-zero state so that a
    replay is not contaminated by a previous run.
    """

    name = "app"

    def reset(self) -> None:
        """Clear all per-run state."""

    def on_frame(self, frame: Frame) -> Any:
        """Consume one frame; return whatever this application produces."""
        raise NotImplementedError

    def summary(self) -> str:
        return ""


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------


def _geometry_from_cfg(cfg_path: Optional[str]):
    if not cfg_path or not os.path.exists(cfg_path):
        return None
    return parse_cfg(cfg_path).geometry()


def iter_dat(dat_path: str, cfg_path: Optional[str] = None) -> Iterator[Frame]:
    """Frames from a recording, in wire order.

    Streams rather than materialising the list: a 60 s TLV-5 capture is a few
    hundred MB and an application only ever needs one frame at a time.
    """
    geom = _geometry_from_cfg(cfg_path)
    with open(dat_path, "rb") as f:
        buf = f.read()
    return iter_frames(buf, geometry=geom)


def run_offline(app: AppModule,
                dat_path: str,
                cfg_path: Optional[str] = None,
                on_result: Optional[Callable[[Frame, Any], None]] = None,
                limit: int = 0) -> int:
    """Replay a recording through `app`.  Returns the number of frames fed."""
    app.reset()
    n = 0
    for frame in iter_dat(dat_path, cfg_path):
        result = app.on_frame(frame)
        if on_result is not None:
            on_result(frame, result)
        n += 1
        if limit and n >= limit:
            break
    return n


def run_live(app: AppModule,
             cfg: str,
             cli: Optional[str] = None,
             data: Optional[str] = None,
             out: str = "./runs",
             label: Optional[str] = None,
             seconds: float = 0.0,
             countdown: int = 0,
             send_config: bool = True,
             on_result: Optional[Callable[[Frame, Any], None]] = None,
             verbose: bool = True):
    """Drive `app` from the sensor, recording the raw stream as it goes.

    `send_config=False` is for firmware that configures itself and rejects a
    host config -- TI's gesture binary is the case that matters here; its
    chirp profile is compiled in (cli.c:88) and the visualizer's own
    instruction is "Start without Send Configuration".  The .cfg is then used
    only to derive geometry for the parser and to document the run.

    Returns the LiveCapture so the caller can read `.summary()` and the paths.
    """
    from ...extraction.live import LiveCapture, resolve_ports

    cli, data = resolve_ports(cli, data)
    if not cli or not data:
        raise SystemExit(
            "Could not determine both COM ports (CLI=%s DATA=%s). Run\n"
            "  python -m mmwave_suite.extraction.live --list-ports"
            % (cli, data))

    app.reset()
    cap = LiveCapture(cli, data, cfg, out, label=label, verbose=verbose)
    cap.countdown = countdown
    cap.seconds = seconds

    def _on_frame(fr: Frame) -> None:
        result = app.on_frame(fr)
        if on_result is not None:
            on_result(fr, result)

    if not send_config:
        _neuter_config(cap)

    cap.start(on_frame=_on_frame)
    warned = False
    try:
        deadline = time.perf_counter() + seconds if seconds > 0 else None
        while deadline is None or time.perf_counter() < deadline:
            time.sleep(0.2)
            if not warned:
                w = cap.check_health()
                if w:
                    warned = True
                    print("[app] WARNING: %s" % w)
    except KeyboardInterrupt:
        pass
    finally:
        cap.stop()
    return cap


def _neuter_config(cap) -> None:
    """Make LiveCapture handshake and record but send no configuration.

    TI's gesture firmware has no CLI config table for the chirp -- it replays
    a compiled-in command list at boot (cli.c:USE_HARD_CODED_CONFIG).  Sending
    a profileCfg to it gets an error per line and, worse, `sensorStop` at the
    top of a normal .cfg would stop a sensor that will not restart on command.
    So the link is opened and the stream is recorded, but nothing is written
    to the CLI port.
    """
    cap.link.send_config = lambda *a, **k: None       # type: ignore[assignment]
    cap.link.sensor_stop = lambda *a, **k: None       # type: ignore[assignment]
