"""
highfidelity/bandwidth.py -- the UART budget, and what fits inside it.

Everything in this folder exists because of one number: the data UART carries
**92,160 bytes per second** at 921600 baud. That is the whole constraint.

Think of it as an SD-card write speed. You can shoot 4K at 8 fps or 1080p at
30 fps, but the card does not care which you chose -- it only cares about bytes
per second. Ask for more than it can take and you do not get a warning, you get
torn frames.

What each payload costs per frame:

    TLV 1  point cloud       16 B x numDetectedObj          (tiny, ~80 B)
    TLV 7  side info          4 B x numDetectedObj          (tiny, ~20 B)
    TLV 2  range profile      2 B x numRangeBins            (cheap, 256 B)
    TLV 3  noise profile      2 B x numRangeBins            (cheap)
    TLV 5  range-Doppler      2 B x numRangeBins x numDopplerBins   (EXPENSIVE)
    TLV 4  azimuth heat map   4 B x numTxAzimAnt x numRxAnt x numRangeBins
    TLV 8  azim-elev heat map 4 B x numVirtualAnt x numRangeBins
    TLV 6  stats             24 B
    TLV 9  temperature       28 B
    + 40 B frame header, + up to 31 B of 32-byte alignment padding

The two heat maps are the only payloads that scale with a PRODUCT of two
dimensions, which is why they dominate and why they force a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

DEFAULT_BAUD = 921600
BITS_PER_BYTE_ON_WIRE = 10          # 8N1: 1 start + 8 data + 1 stop

# Leave headroom. At 100% the link has no slack for jitter in host scheduling,
# and the failure mode is a torn frame rather than a graceful slowdown.
DEFAULT_SAFETY = 0.75

FRAME_HEADER_BYTES = 40
PACKET_ALIGNMENT = 32
TLV_HEADER_BYTES = 8


@dataclass
class PayloadCost:
    name: str
    tlv: int
    bytes_per_frame: int
    note: str = ""


@dataclass
class BudgetReport:
    baud: int
    fps: float
    safety: float
    payloads: List[PayloadCost]

    @property
    def bytes_per_second_link(self) -> float:
        return self.baud / BITS_PER_BYTE_ON_WIRE

    @property
    def budget(self) -> float:
        return self.bytes_per_second_link * self.safety

    @property
    def bytes_per_frame(self) -> int:
        body = sum(p.bytes_per_frame + TLV_HEADER_BYTES
                   for p in self.payloads if p.bytes_per_frame > 0)
        total = FRAME_HEADER_BYTES + body
        # the demo rounds the packet up to a 32-byte boundary
        rem = total % PACKET_ALIGNMENT
        if rem:
            total += PACKET_ALIGNMENT - rem
        return total

    @property
    def bytes_per_second(self) -> float:
        return self.bytes_per_frame * self.fps

    @property
    def utilisation(self) -> float:
        return self.bytes_per_second / self.bytes_per_second_link

    @property
    def fits(self) -> bool:
        return self.bytes_per_second <= self.budget

    @property
    def max_fps(self) -> float:
        return self.budget / self.bytes_per_frame if self.bytes_per_frame else 0.0

    def report(self) -> str:
        L = ["UART budget: %d baud = %.0f B/s, usable at %.0f%% = %.0f B/s"
             % (self.baud, self.bytes_per_second_link, 100 * self.safety,
                self.budget),
             "",
             "%-30s %6s %10s %10s" % ("payload", "TLV", "B/frame", "B/s")]
        L.append("-" * 60)
        for p in self.payloads:
            if p.bytes_per_frame <= 0:
                continue
            L.append("%-30s %6d %10d %10.0f"
                     % (p.name, p.tlv, p.bytes_per_frame + TLV_HEADER_BYTES,
                        (p.bytes_per_frame + TLV_HEADER_BYTES) * self.fps))
        L.append("-" * 60)
        L.append("%-30s %6s %10d %10.0f"
                 % ("TOTAL (incl. header + padding)", "", self.bytes_per_frame,
                    self.bytes_per_second))
        L.append("")
        L.append("At %.1f fps this is %.0f%% of the link (%.1fx the safe budget)."
                 % (self.fps, 100 * self.utilisation,
                    self.bytes_per_second / self.budget))
        if self.fits:
            L.append("VERDICT: FITS. Headroom to %.1f fps." % self.max_fps)
        else:
            L.append("VERDICT: DOES NOT FIT. Max sustainable rate is %.1f fps."
                     % self.max_fps)
            L.append("         Above that the device tears frames; the audit "
                     "will show mid-stream resyncs.")
        return "\n".join(L)


def payload_costs(num_range_bins: int,
                  num_doppler_bins: int,
                  num_virtual_ant: int,
                  num_tx_azim_ant: int = 2,
                  num_rx_ant: int = 4,
                  num_detected_obj: int = 8,
                  detected_objects: int = 1,
                  log_mag_range: int = 1,
                  noise_profile: int = 0,
                  range_azimuth_heatmap: int = 0,
                  range_doppler_heatmap: int = 0,
                  stats_info: int = 1,
                  aop: bool = True) -> List[PayloadCost]:
    """Per-frame byte cost of each payload a guiMonitor line would enable."""
    out: List[PayloadCost] = []
    if detected_objects in (1, 2):
        out.append(PayloadCost("detected points", 1, 16 * num_detected_obj,
                               "16 B/point"))
    if detected_objects == 1:
        out.append(PayloadCost("side info (snr/noise)", 7, 4 * num_detected_obj,
                               "4 B/point"))
    if log_mag_range:
        out.append(PayloadCost("range profile", 2, 2 * num_range_bins))
    if noise_profile:
        out.append(PayloadCost("noise profile", 3, 2 * num_range_bins))
    if range_azimuth_heatmap:
        if aop:
            out.append(PayloadCost("azim-elev heat map (AOP)", 8,
                                   4 * num_virtual_ant * num_range_bins,
                                   "complex int16 per virtual antenna"))
        else:
            out.append(PayloadCost("azimuth heat map", 4,
                                   4 * num_tx_azim_ant * num_rx_ant * num_range_bins,
                                   "complex int16"))
    if range_doppler_heatmap:
        out.append(PayloadCost("range-Doppler heat map", 5,
                               2 * num_range_bins * num_doppler_bins,
                               "uint16 per cell -- scales as a PRODUCT"))
    if stats_info:
        out.append(PayloadCost("stats", 6, 24))
        out.append(PayloadCost("temperature", 9, 28))
    return out


def check(num_range_bins: int, num_doppler_bins: int, fps: float,
          num_virtual_ant: int = 12, baud: int = DEFAULT_BAUD,
          safety: float = DEFAULT_SAFETY, **gui) -> BudgetReport:
    """Budget report for one candidate configuration."""
    return BudgetReport(
        baud=baud, fps=fps, safety=safety,
        payloads=payload_costs(num_range_bins, num_doppler_bins,
                               num_virtual_ant, **gui))


def max_fps_for(num_range_bins: int, num_doppler_bins: int,
                num_virtual_ant: int = 12, baud: int = DEFAULT_BAUD,
                safety: float = DEFAULT_SAFETY, **gui) -> float:
    r = check(num_range_bins, num_doppler_bins, 1.0, num_virtual_ant,
              baud, safety, **gui)
    return r.max_fps


def survey(num_virtual_ant: int = 12, baud: int = DEFAULT_BAUD,
           safety: float = DEFAULT_SAFETY,
           range_bins=(32, 64, 128, 256),
           doppler_bins=(16, 32, 64, 128),
           min_fps: float = 4.0) -> str:
    """What geometries support which heat maps, and at what frame rate."""
    L = ["Max sustainable frame rate (fps) at %d baud, %.0f%% safety, "
         "%d virtual antennas" % (baud, 100 * safety, num_virtual_ant),
         "Point cloud + range profile + stats always enabled.",
         ""]
    for label, gui in (
        ("TLV 5 range-Doppler heat map", dict(range_doppler_heatmap=1)),
        ("TLV 8 azim-elev heat map", dict(range_azimuth_heatmap=1)),
        ("BOTH heat maps", dict(range_doppler_heatmap=1,
                                range_azimuth_heatmap=1)),
    ):
        L.append(label)
        L.append("  %-12s" % "rng \\ dop" + "".join("%9d" % d for d in doppler_bins))
        for nr in range_bins:
            row = ["  %-12d" % nr]
            for nd in doppler_bins:
                f = max_fps_for(nr, nd, num_virtual_ant, baud, safety, **gui)
                row.append("%9s" % ("%.1f" % f if f >= min_fps else "--"))
            L.append("".join(row))
        L.append("")
    L.append("'--' means below %.0f fps: too slow to be useful." % min_fps)
    L.append("Note TLV 8 does NOT depend on numDopplerBins -- it is a "
             "zero-Doppler slice, so its cost is flat across each row.")
    return "\n".join(L)


if __name__ == "__main__":
    print(survey())
