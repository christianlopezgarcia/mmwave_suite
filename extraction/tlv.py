"""
tlv.py -- Complete, order-agnostic, sign-correct parser for the TI mmWave demo
UART output stream (mmWave SDK 3.x, xWR6843 / xWR68xx_AOP and relatives).

This is a direct port of the parsing logic actually executed by the TI GUI
Composer visualizer, read out of:

    C:\\ti\\guicomposer\\runtime\\gcruntime.v11\\mmWave_Demo_Visualizer\\app\\mmWave.js

with the display-layer behaviour stripped out and the known defects of TI's
own reference Python parser (parser_mmw_demo.py) fixed.  Cross-references to
the JS are given as `mmWave.js:<line>` so every decision here is auditable.

Design rules:
  * Nothing is ever silently dropped.  Every deviation from the expected
    structure is recorded on `Frame.anomalies` / `StreamStats`.
  * TLVs are dispatched by type, never by position.  TI's reference Python
    parser assumes TLV#1 is type 1 and TLV#2 is type 7; that assumption breaks
    the moment `guiMonitor` enables a range profile or heat map.
  * int16 fields are decoded as *signed*.  TI's reference Python parser reads
    the SNR / noise side-info as unsigned, which turns any negative value into
    ~6553.5 dB.
  * A frame with zero detected points is a valid frame, not a parse failure.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Wire format constants
# ---------------------------------------------------------------------------

# mmWave.js:2095 isMagic() -- byte sequence, i.e. uint16 LE 0x0102 0x0304 0x0506 0x0708
MAGIC = bytes([2, 1, 4, 3, 6, 5, 8, 7])

# mmWave.js:2222-2263 -- magic(8) + 8 x uint32
FRAME_HEADER_LEN = 40

_HEADER_STRUCT = struct.Struct("<8s8I")

# Sanity bound used to reject false-positive magic words that occur inside
# float payload data.  A single mmw-demo frame cannot plausibly exceed this.
MAX_PLAUSIBLE_FRAME_BYTES = 4 * 1024 * 1024

# MMWDEMO_OUTPUT_MSG_SEGMENT_LEN.  The demo rounds every frame's totalPacketLen
# up to a multiple of this before transmitting, leaving uninitialised padding
# after the last TLV.  Verified empirically: every totalPacketLen observed is
# 0 mod 32, with 0..28 bytes of non-zero pad after the final TLV.
PACKET_ALIGNMENT = 32


class TLVType(IntEnum):
    """mmWave.js:2199-2210"""

    DETECTED_POINTS = 1
    RANGE_PROFILE = 2
    NOISE_PROFILE = 3
    AZIMUTH_STATIC_HEAT_MAP = 4
    RANGE_DOPPLER_HEAT_MAP = 5
    STATS = 6
    DETECTED_POINTS_SIDE_INFO = 7
    AZIMUTH_ELEVATION_STATIC_HEAT_MAP = 8
    TEMPERATURE_STATS = 9


# DPIF_PointCloudCartesian -- mmWave.js:2569-2579.  16 bytes, float32 LE.
POINT_DTYPE = np.dtype(
    [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("doppler", "<f4")]
)

# DPIF_PointCloudSideInfo -- mmWave.js:3339-3346.  4 bytes, *signed* int16 LE,
# both in 0.1 dB steps (mmWave.js:3362, 3374).
SIDEINFO_DTYPE = np.dtype([("snr", "<i2"), ("noise", "<i2")])

SIDE_INFO_DB_SCALE = 0.1


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class FrameHeader:
    version: int
    total_packet_len: int
    platform: int
    frame_number: int
    time_cpu_cycles: int
    num_detected_obj: int
    num_tlvs: int
    sub_frame_number: int

    @property
    def sdk_version_uint16(self) -> int:
        """(major << 8) | minor.

        mmWave.js:2226 -- version is uint32 = major<<24|minor<<16|bugfix<<8|build,
        so little-endian byte[2] is minor and byte[3] is major.
        """
        return ((self.version >> 24) & 0xFF) << 8 | ((self.version >> 16) & 0xFF)

    @property
    def sdk_version_str(self) -> str:
        return "%d.%d.%d.%d" % (
            (self.version >> 24) & 0xFF,
            (self.version >> 16) & 0xFF,
            (self.version >> 8) & 0xFF,
            self.version & 0xFF,
        )

    @property
    def platform_str(self) -> str:
        """e.g. platform word 0xA6843 -> 'xWR6843' (TI prefixes the part
        number with a nibble that is not part of the name)."""
        h = "%X" % self.platform
        return "xWR" + (h[1:] if h.startswith("A") and len(h) > 4 else h)


@dataclass
class Frame:
    header: FrameHeader
    byte_offset: int
    """Absolute offset of the magic word within the source buffer/file."""

    tlv_order: Tuple[int, ...] = ()
    """TLV type codes in the exact order they appeared on the wire."""

    points: Optional[np.ndarray] = None          # POINT_DTYPE
    side_info: Optional[np.ndarray] = None       # SIDEINFO_DTYPE (raw int16)
    range_profile: Optional[np.ndarray] = None   # uint16 raw
    noise_profile: Optional[np.ndarray] = None   # uint16 raw
    azimuth_heatmap: Optional[np.ndarray] = None       # complex64
    azimuth_elev_heatmap: Optional[np.ndarray] = None  # complex64
    range_doppler_heatmap: Optional[np.ndarray] = None  # uint16, raw row-major
    stats: Optional[Dict[str, int]] = None
    temperature: Optional[Dict[str, int]] = None
    unknown_tlvs: Dict[int, bytes] = field(default_factory=dict)

    padding_bytes: int = 0
    """Bytes of 32-byte-alignment padding after the last TLV. Expected, not an
    error -- see PACKET_ALIGNMENT."""

    anomalies: List[str] = field(default_factory=list)

    host_timestamp: Optional[float] = None
    """Wall-clock time.perf_counter() at which the last byte of this frame was
    read from the serial port.  None for offline parsing."""

    # -- convenience -------------------------------------------------------

    @property
    def num_points(self) -> int:
        return 0 if self.points is None else int(self.points.shape[0])

    @property
    def snr_db(self) -> Optional[np.ndarray]:
        """CFAR cell-to-side-noise ratio in dB.  Signed. mmWave.js:3362."""
        if self.side_info is None:
            return None
        return self.side_info["snr"].astype(np.float32) * SIDE_INFO_DB_SCALE

    @property
    def noise_db(self) -> Optional[np.ndarray]:
        """CFAR noise level in dB.  Signed. mmWave.js:3374."""
        if self.side_info is None:
            return None
        return self.side_info["noise"].astype(np.float32) * SIDE_INFO_DB_SCALE

    @property
    def peak_val_db(self) -> Optional[np.ndarray]:
        """Reconstructed per-point peak value.

        peakVal is *not* transmitted in the point cloud.  The visualizer
        reconstructs it as snrDB + noiseDB (mmWave.js:2672) and uses it as the
        'intensity' colour axis.  Undocumented in the SDK stream spec.
        """
        if self.side_info is None:
            return None
        return self.snr_db + self.noise_db

    @property
    def range_m(self) -> Optional[np.ndarray]:
        """Slant range per point, sqrt(x^2+y^2+z^2).  mmWave.js:2593."""
        if self.points is None:
            return None
        p = self.points
        return np.sqrt(
            p["x"].astype(np.float64) ** 2
            + p["y"].astype(np.float64) ** 2
            + p["z"].astype(np.float64) ** 2
        )

    @property
    def azimuth_deg(self) -> Optional[np.ndarray]:
        """Azimuth angle, degrees, measured from boresight (+y).

        Uses atan2 rather than TI's atan(x/y): atan collapses the y<0
        half-plane onto the y>0 half-plane, so a target behind the array is
        reported at the mirrored forward angle.  For through-wall multipath
        work that distinction matters.
        """
        if self.points is None:
            return None
        return np.degrees(
            np.arctan2(self.points["x"].astype(np.float64),
                       self.points["y"].astype(np.float64))
        )

    @property
    def elevation_deg(self) -> Optional[np.ndarray]:
        if self.points is None:
            return None
        p = self.points
        xy = np.hypot(p["x"].astype(np.float64), p["y"].astype(np.float64))
        return np.degrees(np.arctan2(p["z"].astype(np.float64), xy))


@dataclass
class StreamStats:
    """Byte-level accounting for a whole stream/file."""

    frames_ok: int = 0
    frames_with_zero_points: int = 0
    bytes_total: int = 0
    bytes_in_frames: int = 0
    bytes_skipped: int = 0
    """Bytes that were not part of any accepted frame -- i.e. genuinely lost or
    corrupt.  A healthy capture started at a frame boundary has ~0 here."""
    resyncs: int = 0
    false_magic_rejected: int = 0
    truncated_tail_bytes: int = 0
    padded_frames: int = 0
    padding_bytes_total: int = 0
    tlv_type_counts: Dict[int, int] = field(default_factory=dict)
    tlv_order_counts: Dict[Tuple[int, ...], int] = field(default_factory=dict)
    anomaly_counts: Dict[str, int] = field(default_factory=dict)

    def note_anomaly(self, msg: str) -> None:
        key = msg.split(" (")[0]
        self.anomaly_counts[key] = self.anomaly_counts.get(key, 0) + 1


# ---------------------------------------------------------------------------
# Geometry / sizing context
# ---------------------------------------------------------------------------


@dataclass
class Geometry:
    """Everything needed to size the fixed-length TLVs.

    Populate from cfgparse.parse_cfg(); the point cloud and side info are
    self-sizing and parse fine with geometry=None.
    """

    num_range_bins: int = 0
    num_doppler_bins: int = 0
    num_tx_azim_ant: int = 0
    num_rx_ant: int = 0
    num_virtual_ant: int = 0

    @property
    def azimuth_heatmap_bytes(self) -> int:
        # mmWave.js:2942-2944
        return self.num_tx_azim_ant * self.num_rx_ant * self.num_range_bins * 4

    @property
    def range_doppler_bytes(self) -> int:
        # mmWave.js:3299
        return self.num_doppler_bins * self.num_range_bins * 2


# ---------------------------------------------------------------------------
# Frame scanning
# ---------------------------------------------------------------------------


def find_magic(buf: bytes, start: int = 0) -> int:
    """Index of the next magic word at or after `start`, or -1."""
    return buf.find(MAGIC, start)


def _read_header(buf, off: int) -> FrameHeader:
    (_magic, version, total_len, platform, frame_no,
     cycles, num_obj, num_tlvs, sub_frame) = _HEADER_STRUCT.unpack_from(buf, off)
    return FrameHeader(version, total_len, platform, frame_no,
                       cycles, num_obj, num_tlvs, sub_frame)


def _header_is_plausible(hdr: FrameHeader, remaining: int) -> bool:
    """Reject false-positive magic words occurring inside float payload data.

    The magic byte sequence can and does occur by chance inside a point cloud;
    TI's own chunk-splitting logic does not guard against this.
    """
    if not (FRAME_HEADER_LEN <= hdr.total_packet_len <= MAX_PLAUSIBLE_FRAME_BYTES):
        return False
    if hdr.total_packet_len > remaining:
        return False
    if hdr.num_tlvs > 64:
        return False
    if hdr.sub_frame_number > 3:
        return False
    # numDetectedObj is uint32 but is bounded by the demo's point-cloud buffer.
    if hdr.num_detected_obj > 4096:
        return False
    return True


def iter_frames(
    buf: bytes,
    geometry: Optional[Geometry] = None,
    stats: Optional[StreamStats] = None,
    start: int = 0,
) -> Iterator[Frame]:
    """Yield every well-formed frame in `buf`, in order.

    Resynchronises on corruption instead of aborting, and accounts for every
    byte so you can prove nothing was silently lost.
    """
    if stats is None:
        stats = StreamStats()
    stats.bytes_total = len(buf)

    n = len(buf)
    pos = find_magic(buf, start)
    if pos < 0:
        stats.bytes_skipped += max(0, n - start)
        return
    if pos > start:
        # Capture began mid-frame (TI starts writing the .dat on whatever chunk
        # arrives first -- mmWave.js:305 -- so this is normal for TI recordings).
        stats.bytes_skipped += pos - start

    while pos + FRAME_HEADER_LEN <= n:
        hdr = _read_header(buf, pos)

        if not _header_is_plausible(hdr, n - pos):
            if hdr.total_packet_len > n - pos and FRAME_HEADER_LEN <= hdr.total_packet_len <= MAX_PLAUSIBLE_FRAME_BYTES:
                # Genuine frame, file just ends here.
                stats.truncated_tail_bytes += n - pos
                return
            stats.false_magic_rejected += 1
            nxt = find_magic(buf, pos + 1)
            if nxt < 0:
                stats.bytes_skipped += n - pos
                return
            stats.bytes_skipped += nxt - pos
            stats.resyncs += 1
            pos = nxt
            continue

        end = pos + hdr.total_packet_len
        frame = _parse_tlvs(buf, pos, hdr, geometry, stats)
        stats.frames_ok += 1
        stats.bytes_in_frames += hdr.total_packet_len
        if frame.num_points == 0:
            stats.frames_with_zero_points += 1
        stats.tlv_order_counts[frame.tlv_order] = (
            stats.tlv_order_counts.get(frame.tlv_order, 0) + 1
        )
        for a in frame.anomalies:
            stats.note_anomaly(a)
        yield frame

        # The next frame should start exactly at `end`.  If it does not, the
        # stream lost bytes -- record how many rather than hiding it.
        if end >= n:
            return
        if buf[end:end + 8] == MAGIC:
            pos = end
            continue
        nxt = find_magic(buf, end)
        if nxt < 0:
            stats.bytes_skipped += n - end
            return
        stats.bytes_skipped += nxt - end
        stats.resyncs += 1
        pos = nxt


# ---------------------------------------------------------------------------
# TLV dispatch
# ---------------------------------------------------------------------------


def _parse_tlvs(
    buf,
    frame_start: int,
    hdr: FrameHeader,
    geom: Optional[Geometry],
    stats: StreamStats,
) -> Frame:
    frame = Frame(header=hdr, byte_offset=frame_start)
    frame_end = frame_start + hdr.total_packet_len
    off = frame_start + FRAME_HEADER_LEN
    order: List[int] = []

    for i in range(hdr.num_tlvs):
        if off + 8 > frame_end:
            frame.anomalies.append(
                "tlv_header_overruns_frame (tlv %d of %d)" % (i, hdr.num_tlvs))
            break
        tlv_type, tlv_len = struct.unpack_from("<II", buf, off)
        payload = off + 8

        # ---- TLV length convention check --------------------------------
        # SDK 3.x mmw demo emits `length` = payload bytes, EXCLUDING the 8-byte
        # TLV header (confirmed by mmWave.js:2296/2327: idx advances by 8 then
        # by tlvlength).  Some other TI demos/SDKs include the header.  Detect
        # rather than assume.
        if payload + tlv_len > frame_end:
            if tlv_len >= 8 and payload + tlv_len - 8 <= frame_end:
                frame.anomalies.append(
                    "tlv_length_includes_header (type %d)" % tlv_type)
                tlv_len -= 8
            else:
                frame.anomalies.append(
                    "tlv_payload_overruns_frame (type %d len %d)" % (tlv_type, tlv_len))
                break

        order.append(tlv_type)
        stats.tlv_type_counts[tlv_type] = stats.tlv_type_counts.get(tlv_type, 0) + 1

        _decode_tlv(buf, tlv_type, payload, tlv_len, hdr, geom, frame)
        off = payload + tlv_len

    frame.tlv_order = tuple(order)

    if len(order) != hdr.num_tlvs:
        frame.anomalies.append(
            "tlv_count_mismatch (header says %d, parsed %d)" % (hdr.num_tlvs, len(order)))

    # ---- 32-byte packet padding -----------------------------------------
    # The mmw demo rounds totalPacketLen up to a multiple of
    # MMWDEMO_OUTPUT_MSG_SEGMENT_LEN (32).  The pad is NOT zero-filled -- it is
    # whatever was left in the transmit buffer -- so it can contain arbitrary
    # bytes, including a magic-word sequence.  This is why frame boundaries must
    # be chained through totalPacketLen and never rediscovered by scanning for
    # magic words.  Undocumented in the stream spec.
    trailing = frame_end - off
    if trailing > 0:
        if trailing < PACKET_ALIGNMENT and hdr.total_packet_len % PACKET_ALIGNMENT == 0:
            frame.padding_bytes = trailing
            stats.padded_frames += 1
            stats.padding_bytes_total += trailing
        else:
            frame.anomalies.append("trailing_bytes_in_frame (%d)" % trailing)

    return frame


def _decode_tlv(buf, tlv_type: int, payload: int, tlv_len: int,
                hdr: FrameHeader, geom: Optional[Geometry], frame: Frame) -> None:

    if tlv_type == TLVType.DETECTED_POINTS:
        expect = hdr.num_detected_obj * POINT_DTYPE.itemsize
        if tlv_len != expect:
            frame.anomalies.append(
                "point_cloud_len_mismatch (numDetectedObj=%d implies %d bytes, TLV says %d)"
                % (hdr.num_detected_obj, expect, tlv_len))
        count = tlv_len // POINT_DTYPE.itemsize
        frame.points = np.frombuffer(buf, dtype=POINT_DTYPE, count=count, offset=payload)

    elif tlv_type == TLVType.DETECTED_POINTS_SIDE_INFO:
        expect = hdr.num_detected_obj * SIDEINFO_DTYPE.itemsize
        if tlv_len != expect:
            frame.anomalies.append(
                "side_info_len_mismatch (expected %d, got %d)" % (expect, tlv_len))
        count = tlv_len // SIDEINFO_DTYPE.itemsize
        frame.side_info = np.frombuffer(buf, dtype=SIDEINFO_DTYPE, count=count, offset=payload)

    elif tlv_type == TLVType.RANGE_PROFILE:
        frame.range_profile = np.frombuffer(buf, dtype="<u2", count=tlv_len // 2, offset=payload)
        _check_bins(frame, geom, "range_profile", tlv_len // 2)

    elif tlv_type == TLVType.NOISE_PROFILE:
        frame.noise_profile = np.frombuffer(buf, dtype="<u2", count=tlv_len // 2, offset=payload)
        _check_bins(frame, geom, "noise_profile", tlv_len // 2)

    elif tlv_type == TLVType.AZIMUTH_STATIC_HEAT_MAP:
        frame.azimuth_heatmap = _decode_cmplx16(buf, payload, tlv_len, geom, frame,
                                                geom.num_tx_azim_ant * geom.num_rx_ant if geom else 0)

    elif tlv_type == TLVType.AZIMUTH_ELEVATION_STATIC_HEAT_MAP:
        frame.azimuth_elev_heatmap = _decode_cmplx16(buf, payload, tlv_len, geom, frame,
                                                     geom.num_virtual_ant if geom else 0)

    elif tlv_type == TLVType.RANGE_DOPPLER_HEAT_MAP:
        raw = np.frombuffer(buf, dtype="<u2", count=tlv_len // 2, offset=payload)
        if geom and geom.num_doppler_bins and geom.num_range_bins:
            expect = geom.num_doppler_bins * geom.num_range_bins
            if raw.size == expect:
                # mmWave.js:3306 calls MyUtil.reshape(v, numDopplerBins, numRangeBins),
                # and MyUtil.reshape (myutil.js:87-102) is a MATLAB *column-major*
                # reshape: `i = c*rows + r`.  So on the wire the DOPPLER index
                # varies fastest and range is the outer index -- the SDK's
                # detMatrix is [range][doppler].  A plain C-order
                # reshape(numDopplerBins, numRangeBins) has the same shape but
                # transposes the data, scrambling ~99.6% of cells.
                raw = np.ascontiguousarray(
                    raw.reshape(geom.num_range_bins, geom.num_doppler_bins).T)
            else:
                frame.anomalies.append(
                    "rd_heatmap_size_mismatch (expected %d, got %d)" % (expect, raw.size))
        frame.range_doppler_heatmap = raw

    elif tlv_type == TLVType.STATS:
        # mmWave.js:3419-3435 -- 6 x uint32
        if tlv_len >= 24:
            v = struct.unpack_from("<6I", buf, payload)
            frame.stats = dict(
                inter_frame_processing_time_us=v[0],
                transmit_output_time_us=v[1],
                inter_frame_processing_margin_us=v[2],
                inter_chirp_processing_margin_us=v[3],
                active_frame_cpu_load_pct=v[4],
                inter_frame_cpu_load_pct=v[5],
            )
        else:
            frame.anomalies.append("stats_tlv_too_short (%d)" % tlv_len)

    elif tlv_type == TLVType.TEMPERATURE_STATS:
        # mmWave.js:3386-3409 -- int32 valid, uint32 time, then 10 x *signed* int16
        if tlv_len >= 28:
            valid, t_ms = struct.unpack_from("<iI", buf, payload)
            s = struct.unpack_from("<10h", buf, payload + 8)
            frame.temperature = dict(
                report_valid=valid, time_ms=t_ms,
                rx0=s[0], rx1=s[1], rx2=s[2], rx3=s[3],
                tx0=s[4], tx1=s[5], tx2=s[6],
                pm=s[7], dig0=s[8], dig1=s[9],
            )
        else:
            frame.anomalies.append("temperature_tlv_too_short (%d)" % tlv_len)

    else:
        frame.unknown_tlvs[tlv_type] = bytes(buf[payload:payload + tlv_len])
        frame.anomalies.append("unknown_tlv_type (%d, %d bytes)" % (tlv_type, tlv_len))


def _check_bins(frame: Frame, geom: Optional[Geometry], name: str, got: int) -> None:
    if geom and geom.num_range_bins and got != geom.num_range_bins:
        frame.anomalies.append(
            "%s_bin_count_mismatch (cfg implies %d, stream has %d)"
            % (name, geom.num_range_bins, got))


def _decode_cmplx16(buf, payload: int, tlv_len: int, geom: Optional[Geometry],
                    frame: Frame, n_ant: int) -> np.ndarray:
    """int16 (real, imag) interleaved -> complex64, shaped (range_bins, ant).

    mmWave.js:2961-2965: real = q[i+1]*256 + q[i], sign-corrected at 32767.
    Reading as native little-endian int16 is equivalent and exact.
    """
    iq = np.frombuffer(buf, dtype="<i2", count=tlv_len // 2, offset=payload)
    c = iq[0::2].astype(np.float32) + 1j * iq[1::2].astype(np.float32)
    c = c.astype(np.complex64)
    if geom and n_ant and geom.num_range_bins:
        expect = n_ant * geom.num_range_bins
        if c.size == expect:
            # Antenna index varies fastest within a range bin.
            return c.reshape(geom.num_range_bins, n_ant)
        frame.anomalies.append(
            "heatmap_size_mismatch (expected %d complex, got %d)" % (expect, c.size))
    return c


# ---------------------------------------------------------------------------
# Incremental parser for live streaming
# ---------------------------------------------------------------------------


class StreamParser:
    """Feed it arbitrary UART chunks, get whole frames out.

    Mirrors the reassembly in mmWave.js:298-362 but without the display-layer
    gates that make the visualizer drop frames (`onPlotsTab`, `in_process1`),
    and without the buffer-nuking resync at mmWave.js:317-322.
    """

    def __init__(self, geometry: Optional[Geometry] = None, max_buffer: int = 8 << 20):
        self.geometry = geometry
        self.stats = StreamStats()
        self._buf = bytearray()
        self._max_buffer = max_buffer
        self._abs = 0
        """Absolute stream offset of self._buf[0].  Because the recorder writes
        every received byte verbatim, this is also the offset into the .dat."""

    def feed(self, chunk: bytes, host_timestamp: Optional[float] = None) -> List[Frame]:
        self._buf += chunk
        self.stats.bytes_total += len(chunk)
        out: List[Frame] = []

        while True:
            pos = self._buf.find(MAGIC)
            if pos < 0:
                # Keep only a possible partial magic at the tail.
                if len(self._buf) > 7:
                    n = len(self._buf) - 7
                    self.stats.bytes_skipped += n
                    del self._buf[:-7]
                    self._abs += n
                break
            if pos > 0:
                # Bytes before a magic word are bytes the host lost.  This is a
                # resync, and must be counted as one -- reporting loss only in
                # bytes_skipped while resyncs stays 0 understates the damage.
                self.stats.bytes_skipped += pos
                self.stats.resyncs += 1
                del self._buf[:pos]
                self._abs += pos

            if len(self._buf) < FRAME_HEADER_LEN:
                break

            hdr = _read_header(self._buf, 0)
            if not (FRAME_HEADER_LEN <= hdr.total_packet_len <= MAX_PLAUSIBLE_FRAME_BYTES):
                self.stats.false_magic_rejected += 1
                self.stats.resyncs += 1
                del self._buf[:1]
                self._abs += 1
                continue
            if len(self._buf) < hdr.total_packet_len:
                break  # wait for more bytes

            mv = bytes(self._buf[:hdr.total_packet_len])
            frame = _parse_tlvs(mv, 0, hdr, self.geometry, self.stats)
            frame.byte_offset = self._abs
            frame.host_timestamp = host_timestamp
            self.stats.frames_ok += 1
            self.stats.bytes_in_frames += hdr.total_packet_len
            if frame.num_points == 0:
                self.stats.frames_with_zero_points += 1
            self.stats.tlv_order_counts[frame.tlv_order] = (
                self.stats.tlv_order_counts.get(frame.tlv_order, 0) + 1)
            for a in frame.anomalies:
                self.stats.note_anomaly(a)
            out.append(frame)
            del self._buf[:hdr.total_packet_len]
            self._abs += hdr.total_packet_len

        if len(self._buf) > self._max_buffer:
            self.stats.bytes_skipped += len(self._buf)
            self._abs += len(self._buf)
            self._buf.clear()
            self.stats.note_anomaly("buffer_overflow_reset")

        return out


# ---------------------------------------------------------------------------
# File convenience
# ---------------------------------------------------------------------------


def parse_dat(path: str, geometry: Optional[Geometry] = None
              ) -> Tuple[List[Frame], StreamStats]:
    """Parse a whole .dat recording.  Returns (frames, stats)."""
    with open(path, "rb") as f:
        buf = f.read()
    stats = StreamStats()
    frames = list(iter_frames(buf, geometry=geometry, stats=stats))
    return frames, stats
