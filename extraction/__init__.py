"""
mmwave_direct -- an offline, dependency-light replacement for the TI mmWave
Demo Visualizer data path.

    from mmwave_direct import parse_cfg, parse_dat, StreamParser, RadarLink

Modules:
    tlv       frame + TLV decoding (port of mmWave.js, with TI's Python
              reference-parser defects fixed)
    cfgparse  .cfg -> derived geometry and Q-format scaling (port of
              input_validations.js)
    link      pyserial control of the EVM; no TI Cloud Agent, no network
    live      real-time capture pipeline with byte-complete recording
    audit     prove what is and is not present in a .dat recording
"""

from .tlv import (  # noqa: F401
    MAGIC,
    FRAME_HEADER_LEN,
    TLVType,
    POINT_DTYPE,
    SIDEINFO_DTYPE,
    Frame,
    FrameHeader,
    Geometry,
    StreamParser,
    StreamStats,
    iter_frames,
    parse_dat,
)
from .cfgparse import (  # noqa: F401
    GuiMonitor,
    OnChipFilters,
    ProfileCfg,
    RadarConfig,
    parse_cfg,
)

__all__ = [
    "MAGIC", "FRAME_HEADER_LEN", "TLVType", "POINT_DTYPE", "SIDEINFO_DTYPE",
    "Frame", "FrameHeader", "Geometry", "StreamParser", "StreamStats",
    "iter_frames", "parse_dat",
    "GuiMonitor", "OnChipFilters", "ProfileCfg", "RadarConfig", "parse_cfg",
]

__version__ = "1.0.0"
