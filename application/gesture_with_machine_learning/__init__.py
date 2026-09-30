"""Gesture recognition on the IWR6843AOP -- TI's lab, ported to the host.

Quick start
-----------
TI's firmware flashed (see ti_reference/prebuilt_binaries):

    python -m mmwave_suite.application.gesture_with_machine_learning \
        live --mode onchip

Stock out-of-box firmware, features computed here:

    python -m mmwave_suite.application.gesture_with_machine_learning \
        live --mode points

Read README.md before the second one: the shipped weights are only valid on
TI's firmware, so `points` mode needs a model you trained yourself.
"""

from .ann import CLASS_NAMES, GestureANN
from .pipeline import GesturePipeline, GestureResult
from .postproc import GestureDebouncer

__all__ = ["CLASS_NAMES", "GestureANN", "GesturePipeline", "GestureResult",
           "GestureDebouncer"]
