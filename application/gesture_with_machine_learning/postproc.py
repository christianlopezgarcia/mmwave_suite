"""Turn a stream of per-frame probabilities into discrete gesture events.

Port of `FindGesture()` (gesture.c:575-638).  The rule is deliberately crude
and it is worth understanding why, because it is most of the demo's apparent
accuracy:

  * A class only "votes" in a frame if its probability clears a per-class
    threshold.  The thresholds are not uniform -- 0.6 for the swipes, 0.9 for
    the twirls, 0.99 for shine -- which is TI telling you directly which
    classes their network is unreliable on.
  * A class only fires if it has voted in more than `count` of the last 15
    frames.  Again per-class: 4/15 for swipes, 9/15 for twirls, 8/15 for
    shine.
  * The scan runs low index to high and keeps the LAST class that passes, so
    a higher-numbered class silently wins ties.  Reproduced here, because a
    "tidier" argmax would not match the board.

At 28.6 fps a 15-frame window is 525 ms, so the debouncer alone imposes a
~0.5 s floor on how fast gestures can be issued back to back.

TI's own visualizer (`ti_reference/visualizer/gesture_recognition.py`, which
re-implements this on the host from the TLV-1051 probabilities) uses the same
count thresholds but raises the no-gesture probability threshold from 0.6 to
0.99.  Both sets are provided; `TI_VISUALIZER_PROB_THRESHOLDS` is the better
default when you are consuming probabilities off the wire, since it stops the
idle class from latching over a real gesture that is still ramping up.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np

from .ann import CLASS_NAMES

# gesture.h:103-114
TI_FIRMWARE_PROB_THRESHOLDS = (0.6, 0.6, 0.6, 0.6, 0.6, 0.9, 0.9, 0.6, 0.6, 0.99)
# ti_reference/visualizer/gesture_recognition.py:95
TI_VISUALIZER_PROB_THRESHOLDS = (0.99, 0.6, 0.6, 0.6, 0.6, 0.9, 0.9, 0.6, 0.6, 0.99)
# gesture.h:117-128, identical in both
TI_COUNT_THRESHOLDS = (4, 4, 4, 4, 4, 9, 9, 4, 4, 8)

WINDOW = 15
IDX_NO_GESTURE = 0


class GestureDebouncer:
    """Sliding-window vote over per-class probability thresholds."""

    def __init__(self,
                 prob_thresholds: Sequence[float] = TI_VISUALIZER_PROB_THRESHOLDS,
                 count_thresholds: Sequence[int] = TI_COUNT_THRESHOLDS,
                 window: int = WINDOW,
                 class_names: Sequence[str] = CLASS_NAMES):
        n = len(class_names)
        if len(prob_thresholds) != n or len(count_thresholds) != n:
            raise ValueError("thresholds must have one entry per class (%d)" % n)
        self.prob_thresholds = np.asarray(prob_thresholds, dtype=np.float64)
        self.count_thresholds = np.asarray(count_thresholds, dtype=np.int32)
        self.window = int(window)
        self.class_names = tuple(class_names)
        self.reset()

    def reset(self) -> None:
        self.votes = np.zeros((self.window, len(self.class_names)), dtype=np.int8)
        self.prev = IDX_NO_GESTURE
        self.current = IDX_NO_GESTURE
        self.frame_count = 0
        self.events: List[tuple] = []

    def update(self, probs: np.ndarray) -> Optional[str]:
        """Feed one frame's probabilities.

        Returns the gesture name on the frame where a NEW gesture latches, and
        None otherwise -- i.e. one event per gesture, not one per frame.
        """
        p = np.asarray(probs, dtype=np.float64).reshape(-1)
        if p.size != len(self.class_names):
            raise ValueError("expected %d probabilities, got %d"
                             % (len(self.class_names), p.size))

        self.frame_count += 1
        self.votes[:-1] = self.votes[1:]
        self.votes[-1] = (p >= self.prob_thresholds).astype(np.int8)

        conf = self.votes.sum(axis=0)
        passing = np.nonzero(conf > self.count_thresholds)[0]
        # Last index wins -- gesture.c:610-612 keeps assigning inside the loop.
        self.current = int(passing[-1]) if passing.size else IDX_NO_GESTURE

        fired = None
        if self.current != self.prev and self.current != IDX_NO_GESTURE:
            fired = self.class_names[self.current]
            self.events.append((self.frame_count, fired, float(p[self.current])))
        self.prev = self.current
        return fired

    @property
    def state(self) -> str:
        return self.class_names[self.current]

    def summary(self) -> str:
        if not self.events:
            return "no gestures detected in %d frames" % self.frame_count
        counts = {}
        for _f, name, _p in self.events:
            counts[name] = counts.get(name, 0) + 1
        order = sorted(counts.items(), key=lambda kv: -kv[1])
        return ("%d gesture events in %d frames: %s"
                % (len(self.events), self.frame_count,
                   ", ".join("%s x%d" % (k, v) for k, v in order)))
