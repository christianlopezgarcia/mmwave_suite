"""
link.py -- Direct pyserial control of the mmWave EVM.  No TI Cloud Agent, no
localhost HTTP server, no node-webkit, no network of any kind.

The CLI handshake implemented here is the one the visualizer actually performs,
read out of mmWave.js:4210-4379 (`cmd_sender_listener`):

    1. send `version`          -> accumulate reply until a bare `Done`
    2. send `queryDemoStatus`  -> parse "Data port baud rate" and "Sensor State"
                                  (SDK >= 3.4 only, mmWave.js:4295)
    3. if the device's data-port baud differs from ours AND the sensor is not
       already started (state 2), send `configDataPort <baud> 1`.
       The trailing `1` asks the device to emit an ack on the data port so the
       host can confirm the link.  (mmWave.js:4240-4245)
    4. send the .cfg one line at a time, advancing only on `Done` or `Skipped`
       (mmWave.js:4370-4377).  Comment lines beginning with `%` are sent too --
       TI deliberately does not filter them (the filter at mmWave.js:4218 is
       commented out) and the device answers `Skipped`.

Anything containing "Error " aborts the sequence.

Departures from TI, all deliberate:
  * A missing `version` reply is fatal here after bounded retries.  TI can be
    lax because its whole config path lives inside askVersion's callback and
    simply never fires; ours would run on regardless.
  * If the sensor is already streaming when we attach (state 2 -- the normal
    case after closing the TI Visualizer), we stop it during the handshake.
    Otherwise frames produced by the PREVIOUS configuration are recorded before
    the new .cfg takes effect, and the frame counter visibly resets mid-file.
  * `configDataPort` rejections are detected.  The firmware answers `Done` even
    when it prints `Ignored: ...` and keeps the old baud.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    raise SystemExit("pyserial is required:  pip install pyserial")


DEFAULT_CLI_BAUD = 115200
DEFAULT_DATA_BAUD = 921600

# MMWDEMO_DATAUART_MAX_BAUDRATE_SUPPORTED
DATA_BAUD_DEVICE_MAX = 3125000

PROMPT = "mmwDemo:/>"

SENSOR_STATE_STARTED = 2

# Commands that make the device do real work and can take far longer than a
# CLI round-trip.  A 3 s timeout on sensorStart is how you end up with the EVM
# streaming and the host convinced the config failed.
_SLOW_COMMAND_TIMEOUTS = {
    "sensorstart": 15.0,
    "sensorstop": 15.0,
    "flushcfg": 5.0,
    "calibdata": 15.0,
    "measurerangebiasandrxchanphase": 15.0,
    "compRangeBiasAndRxChanPhase".lower(): 5.0,
}


class ConfigError(RuntimeError):
    pass


class PortError(RuntimeError):
    pass


@dataclass
class DeviceInfo:
    raw: str = ""
    platform: str = ""
    sdk_version: str = ""
    sdk_version_uint16: int = 0
    device_info: str = ""

    @classmethod
    def parse(cls, text: str) -> "DeviceInfo":
        import re
        d = cls(raw=text)
        m = re.search(r"Platform\s*:\s*(\S+)", text)
        if m:
            d.platform = m.group(1)
        m = re.search(r"Device Info\s*:\s*(.*)", text)
        if m:
            d.device_info = m.group(1).strip()
        m = re.search(r"mmWave SDK Version\s*:\s*(\S+)", text)
        if m:
            d.sdk_version = m.group(1)
            parts = d.sdk_version.split(".")
            if len(parts) >= 2:
                try:
                    d.sdk_version_uint16 = (int(parts[0]) << 8) | int(parts[1])
                except ValueError:
                    pass
        return d


@dataclass
class DemoStatus:
    raw: str = ""
    data_port_baud: Optional[int] = None
    sensor_state: Optional[int] = None

    @classmethod
    def parse(cls, text: str) -> "DemoStatus":
        import re
        s = cls(raw=text)
        m = re.search(r"Data port baud rate\s*:\s*(\d+)", text)
        if m:
            s.data_port_baud = int(m.group(1))
        m = re.search(r"Sensor State\s*:\s*(\d+)", text)
        if m:
            s.sensor_state = int(m.group(1))
        return s


# ---------------------------------------------------------------------------
# Port discovery
# ---------------------------------------------------------------------------

# Ports that are never an mmWave EVM.  Intel AMT Serial-over-LAN in particular
# enumerates as a perfectly openable COM port that will never produce a byte.
_EXCLUDE = ("active management", "serial over lan", "sol (com", "bluetooth")

# (substring, role).  Checked in order; first match wins.  Roles:
#   'cli'  -> User/Application/Command port, 115200
#   'data' -> Auxiliary/Data port, 921600
_PORT_HINTS = [
    # TI XDS110 (IWR6843ISK / ODS / most EVM carriers)
    ("xds110 class auxiliary data port", "data"),
    ("auxiliary data port", "data"),
    ("xds110 class application/user uart", "cli"),
    ("application/user uart", "cli"),
    # Silicon Labs dual CP2105 (IWR6843AOPEVM and similar standalone boards).
    # Enhanced = CLI, Standard = DATA.
    ("enhanced com port", "cli"),
    ("standard com port", "data"),
    # FTDI dual-channel carriers
    ("usb serial port", None),
]


def find_ports(verbose: bool = True) -> List[Tuple[str, str]]:
    """List serial ports with their descriptions."""
    out = []
    for p in list_ports.comports():
        out.append((p.device, p.description or ""))
        if verbose:
            print("  %-8s %s" % (p.device, p.description))
    return out


def guess_evm_ports() -> Tuple[Optional[str], Optional[str]]:
    """Best-effort autodetect of (cli_port, data_port).

    Handles the XDS110 pair and the Silicon Labs dual CP2105 pair, and never
    returns a port that is obviously not a radar (Intel AMT SOL, Bluetooth).
    Returns (None, None) rather than a half-guess when it cannot tell.
    """
    cli = data = None
    unknown: List[str] = []

    for p in list_ports.comports():
        desc = (p.description or "").lower()
        if any(x in desc for x in _EXCLUDE):
            continue
        role = None
        for needle, r in _PORT_HINTS:
            if needle in desc:
                role = r
                break
        if role == "cli" and cli is None:
            cli = p.device
        elif role == "data" and data is None:
            data = p.device
        elif role is None:
            unknown.append(p.device)

    # FTDI-style carriers expose two indistinguishable ports; TI's convention is
    # that the lower-numbered one is the CLI.  Only guess when we have exactly
    # two and learned nothing else.
    if cli is None and data is None and len(unknown) == 2:
        unknown.sort()
        cli, data = unknown[0], unknown[1]

    return cli, data


# ---------------------------------------------------------------------------


class RadarLink:
    """Owns both serial ports and the reader thread."""

    def __init__(self,
                 cli_port: str,
                 data_port: str,
                 cli_baud: int = DEFAULT_CLI_BAUD,
                 data_baud: int = DEFAULT_DATA_BAUD,
                 cli_timeout: float = 0.2,
                 rx_buffer_bytes: int = 1 << 20,
                 verbose: bool = True):
        self.cli_port_name = cli_port
        self.data_port_name = data_port
        self.cli_baud = cli_baud
        self.data_baud = data_baud
        self.cli_timeout = cli_timeout
        self.rx_buffer_bytes = rx_buffer_bytes
        self.verbose = verbose

        self.cli: Optional[serial.Serial] = None
        self.data: Optional[serial.Serial] = None

        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.rx_queue: "queue.Queue[Tuple[float, bytes]]" = queue.Queue()
        self.bytes_read = 0
        self.reader_error: Optional[BaseException] = None
        self.rx_buffer_ok: Optional[bool] = None
        self.console_log: List[str] = []

    # -- lifecycle ---------------------------------------------------------

    def _open_one(self, name: str, baud: int, timeout: float, role: str
                  ) -> "serial.Serial":
        try:
            return serial.Serial(name, baud, timeout=timeout)
        except serial.SerialException as e:
            msg = str(e)
            if "access is denied" in msg.lower() or "permission" in msg.lower():
                raise PortError(
                    "Cannot open %s port %s: it is already in use.\n"
                    "  The TI mmWave Demo Visualizer holds both COM ports while "
                    "it is connected.\n"
                    "  Close it (or any other terminal on %s) and try again.\n"
                    "  Original error: %s" % (role, name, name, msg)) from e
            raise PortError(
                "Cannot open %s port %s at %d baud: %s" % (role, name, baud, msg)) from e

    def open(self) -> "RadarLink":
        """Open both ports atomically -- if the second fails, the first is closed."""
        cli = self._open_one(self.cli_port_name, self.cli_baud, self.cli_timeout, "CLI")
        try:
            data = self._open_one(self.data_port_name, self.data_baud, 0.05, "DATA")
        except BaseException:
            cli.close()
            raise
        self.cli, self.data = cli, data

        # A large OS-side buffer is the cheapest insurance against frame loss:
        # at 921600 baud the default 4 KB holds well under a second of stream,
        # so any GUI stall long enough to block the reader drops whole frames.
        try:
            self.data.set_buffer_size(rx_size=self.rx_buffer_bytes)
            self.rx_buffer_ok = True
        except Exception as e:
            self.rx_buffer_ok = False
            self._log("WARNING: could not enlarge the receive buffer on %s (%s). "
                      "Frames may be dropped if the consumer stalls; prefer "
                      "mmwave_direct.live over mmwave_direct.viz for captures "
                      "that matter." % (self.data_port_name, e))

        self._log("opened CLI %s @%d, DATA %s @%d"
                  % (self.cli_port_name, self.cli_baud,
                     self.data_port_name, self.data_baud))
        return self

    def close(self) -> None:
        self.stop_reader()
        for p in (self.cli, self.data):
            if p is not None:
                try:
                    if p.is_open:
                        p.close()
                except Exception:
                    pass
        self.cli = self.data = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        try:
            self.sensor_stop()
        except Exception:
            pass
        self.close()

    def _log(self, msg: str) -> None:
        self.console_log.append(msg)
        if self.verbose:
            print("[link] %s" % msg)

    # -- CLI ---------------------------------------------------------------

    def _read_cli_lines(self, deadline: float):
        """Yield decoded CLI lines until `deadline`."""
        buf = b""
        while time.monotonic() < deadline:
            chunk = self.cli.read(256)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    yield line.decode("utf-8", "replace").strip("\r\n\x00 ")
            else:
                if buf:
                    text = buf.decode("utf-8", "replace")
                    if PROMPT in text:
                        yield text.strip("\r\n\x00 ")
                        buf = b""

    def _drain_cli(self, quiet: float = 0.25, cap: float = 2.0) -> None:
        """Read until the CLI has been silent for `quiet` seconds.

        reset_input_buffer() is PurgeComm on the host driver buffer only; it
        cannot cancel bytes still in flight from the device.  After a timed-out
        command, a late reply would otherwise be mis-read as the NEXT command's
        response and shift the whole config by one line.
        """
        if self.cli is None:
            return
        end = time.monotonic() + cap
        last = time.monotonic()
        while time.monotonic() < end:
            chunk = self.cli.read(256)
            if chunk:
                last = time.monotonic()
            elif time.monotonic() - last >= quiet:
                return
        try:
            self.cli.reset_input_buffer()
        except Exception:
            pass

    @staticmethod
    def _timeout_for(cmd: str, default: float) -> float:
        head = cmd.split()[0].lower() if cmd.split() else ""
        return _SLOW_COMMAND_TIMEOUTS.get(head, default)

    def send_command(self, cmd: str, timeout: Optional[float] = None
                     ) -> Tuple[bool, List[str]]:
        """Send one CLI command; advance on `Done`/`Skipped`, fail on `Error `.

        Returns (ok, response_lines).  On failure the CLI is drained so the next
        command starts on a clean boundary.
        """
        if self.cli is None:
            raise PortError("call open() before send_command()")
        tmo = self._timeout_for(cmd, timeout if timeout is not None else 3.0)
        try:
            self.cli.reset_input_buffer()
        except Exception:
            pass
        self.cli.write((cmd + "\n").encode())
        self.cli.flush()

        lines: List[str] = []
        deadline = time.monotonic() + tmo
        for line in self._read_cli_lines(deadline):
            # The device echoes the command, often glued to the prompt
            # ("mmwDemo:/>sensorStop"), so compare after stripping the prompt.
            stripped = line.replace(PROMPT, "").strip()
            if not stripped or stripped == cmd:
                continue
            lines.append(stripped)
            if stripped in ("Done", "Skipped"):
                return True, lines
            if "Error " in stripped:
                self._drain_cli()
                return False, lines
        self._drain_cli()
        return False, lines + ["<timeout after %.1fs>" % tmo]

    def query_version(self, timeout: float = 3.0, attempts: int = 3) -> DeviceInfo:
        """mmWave.js:4229-4234.  Fatal after bounded retries.

        The dominant cause of a missing reply is launching inside the EVM's boot
        window, so retry before giving up -- but never continue without it: an
        absent version silently disables the whole baud negotiation, and the
        late reply then desynchronises the config by one command.
        """
        last: List[str] = []
        for i in range(attempts):
            ok, lines = self.send_command("version", timeout)
            last = lines
            if ok:
                info = DeviceInfo.parse("\n".join(lines))
                if not info.sdk_version:
                    raise ConfigError(
                        "'version' returned Done but no 'mmWave SDK Version' "
                        "line: %r" % (lines,))
                self._log("device: platform=%s sdk=%s"
                          % (info.platform or "?", info.sdk_version))
                return info
            self._log("'version' attempt %d/%d did not complete "
                      "(device may still be booting)" % (i + 1, attempts))
            self._drain_cli()
        raise ConfigError(
            "No response to 'version' on %s after %d attempts.\n"
            "  Either the EVM is not running the mmWave demo, it is still "
            "booting, or %s is not the CLI port.\n"
            "  On a Silicon Labs dual CP2105 the CLI is the *Enhanced* COM port "
            "and the data stream is the *Standard* one.\n"
            "  Last bytes seen: %r"
            % (self.cli_port_name, attempts, self.cli_port_name, last[-3:]))

    def query_status(self, timeout: float = 3.0) -> DemoStatus:
        """mmWave.js:4235-4239 -- SDK >= 3.4 only."""
        ok, lines = self.send_command("queryDemoStatus", timeout)
        st = DemoStatus.parse("\n".join(lines))
        if ok:
            self._log("device status: data_baud=%s sensor_state=%s"
                      % (st.data_port_baud, st.sensor_state))
        else:
            self._log("WARNING: queryDemoStatus did not complete: %r" % (lines[-2:],))
        return st

    def config_data_port(self, baud: int, ack: int = 1,
                         expect_change: bool = False) -> bool:
        """mmWave.js:4240-4245 -- `configDataPort <baud> <ackPing>`.

        The firmware answers `Done` even when it rejects the request and keeps
        the old baud, printing `Ignored: ...`.  Detect that.
        """
        if baud > DATA_BAUD_DEVICE_MAX:
            self._log("configDataPort %d exceeds the device maximum (%d)"
                      % (baud, DATA_BAUD_DEVICE_MAX))
            return False
        ok, lines = self.send_command("configDataPort %d %d" % (baud, ack))
        body = "\n".join(lines)
        if not ok:
            self._log("configDataPort %d FAILED: %r" % (baud, lines[-2:]))
            return False
        if "Ignored" in body:
            self._log("configDataPort %d REJECTED by device: %s" % (baud, body))
            return False
        # The device prints "changed to N" only when the rate actually changed,
        # so require it only when we asked for a change.
        if expect_change and ("changed to %d" % baud) not in body:
            self._log("configDataPort %d: device never confirmed the change: %s"
                      % (baud, body))
            return False
        self._log("configDataPort %d -> ok" % baud)
        return True

    def handshake(self) -> DeviceInfo:
        """Reproduce the visualizer's pre-config sequence, then make it safe.

        Must be called BEFORE start_reader(): if the sensor is already streaming
        we stop it here, so no frames from the previous configuration end up in
        the recording.
        """
        info = self.query_version()
        if info.sdk_version_uint16 < 0x0304:
            return info                      # queryDemoStatus not available

        st = self.query_status()

        # The normal state after closing the TI Visualizer is "still streaming".
        # TI never has to handle this because it owns the port continuously.
        if st.sensor_state == SENSOR_STATE_STARTED:
            self._log("device is still streaming from a previous session; "
                      "stopping it before we start recording")
            if not self.sensor_stop():
                raise ConfigError(
                    "Device reports sensor_state=2 (streaming) but sensorStop "
                    "failed. Power-cycle the EVM and retry.")
            st = self.query_status()

        if st.data_port_baud is None:
            self._log("WARNING: could not read the device's data-port baud rate; "
                      "assuming %d" % self.data_baud)
            return info

        if st.data_port_baud != self.data_baud:
            if not self.config_data_port(self.data_baud, expect_change=True):
                raise ConfigError(
                    "Device is shipping data at %d baud and would not switch to "
                    "%d. Either rerun with --data-baud %d, or power-cycle the "
                    "EVM." % (st.data_port_baud, self.data_baud, st.data_port_baud))
            check = self.query_status()
            if check.data_port_baud not in (None, self.data_baud):
                raise ConfigError(
                    "configDataPort reported success but the device still "
                    "reports %d baud." % check.data_port_baud)
        else:
            # Baud already matches; still ping so the device opens the data link.
            self.config_data_port(self.data_baud)
        return info

    def sensor_stop(self) -> bool:
        ok, _ = self.send_command("sensorStop")
        return ok

    def sensor_start(self) -> bool:
        ok, _ = self.send_command("sensorStart")
        return ok

    def send_config(self,
                    cfg_lines: List[str],
                    inter_command_delay: float = 0.01,
                    strict: bool = True) -> List[Tuple[str, bool, List[str]]]:
        """Send a whole .cfg, one line at a time, honouring Done/Skipped.

        Comment lines are sent verbatim, matching TI (mmWave.js:4217-4219).
        Per-command timeouts are applied automatically for the slow commands.
        Returns [(command, ok, response_lines), ...].
        """
        results: List[Tuple[str, bool, List[str]]] = []
        for raw in cfg_lines:
            cmd = raw.strip()
            if not cmd:
                continue
            ok, lines = self.send_command(cmd)
            results.append((cmd, ok, lines))
            if not ok:
                msg = "config command failed: %r -> %r" % (cmd, lines[-2:])
                self._log("ERROR: " + msg)
                if strict:
                    raise ConfigError(msg)
            if inter_command_delay:
                time.sleep(inter_command_delay)
        self._log("sent %d config commands" % len(results))
        return results

    # -- data port ---------------------------------------------------------

    def start_reader(self,
                     on_chunk: Optional[Callable[[float, bytes], None]] = None,
                     chunk_size: int = 4096,
                     use_queue: bool = True) -> None:
        """Spawn the data-port reader thread.

        The thread does nothing but read, timestamp and hand off.  All parsing
        happens elsewhere, so a slow consumer can never stall the UART -- which
        is precisely the failure mode the visualizer has (mmWave.js:271 drops
        frames whenever the UI is busy or you are not on the Plots tab).
        """
        if self.data is None:
            raise PortError("call open() before start_reader()")

        # Discard anything the data port accumulated during the handshake.  The
        # `configDataPort <baud> 1` ack is 16 bytes of 0xFF, and any frames the
        # device emitted before sensorStop took effect are from the PREVIOUS
        # configuration.  Neither belongs in the recording; left in place they
        # are counted as lost bytes by the audit.
        try:
            stale = self.data.in_waiting
            if stale:
                self.data.read(stale)
                self._log("discarded %d byte(s) buffered on the data port during "
                          "the handshake (configDataPort ack / stale frames)" % stale)
        except Exception:
            pass

        self._stop.clear()
        self.reader_error = None

        def run():
            port = self.data
            try:
                while not self._stop.is_set():
                    try:
                        waiting = port.in_waiting
                        n = max(chunk_size, waiting) if waiting else chunk_size
                        chunk = port.read(n)
                    except (OSError, serial.SerialException) as e:
                        self.reader_error = e
                        self._log("ERROR: data port read failed: %s" % e)
                        break
                    if not chunk:
                        continue
                    ts = time.perf_counter()
                    self.bytes_read += len(chunk)
                    if on_chunk is not None:
                        # A raising consumer (disk full, closed file) must not
                        # silently kill the reader and leave the capture looking
                        # healthy while the .dat stops growing.
                        try:
                            on_chunk(ts, chunk)
                        except BaseException as e:
                            self.reader_error = e
                            self._log("ERROR: chunk handler raised, stopping "
                                      "reader: %r" % (e,))
                            break
                    if use_queue:
                        self.rx_queue.put((ts, chunk))
            except BaseException as e:  # pragma: no cover
                self.reader_error = e
                self._log("ERROR: reader thread died: %r" % (e,))

        self._reader = threading.Thread(target=run, name="mmwave-reader", daemon=True)
        self._reader.start()
        self._log("reader thread started")

    def reader_alive(self) -> bool:
        return self._reader is not None and self._reader.is_alive()

    def stop_reader(self, timeout: float = 2.0) -> bool:
        """Returns True if the thread actually finished."""
        self._stop.set()
        if self._reader is not None:
            self._reader.join(timeout)
            alive = self._reader.is_alive()
            if alive:
                self._log("WARNING: reader thread did not stop within %.1fs" % timeout)
            self._reader = None
            return not alive
        return True
