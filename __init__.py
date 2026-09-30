"""
mmwave_suite -- one repository for 60 GHz mmWave radar work.

Three parts, deliberately separated:

    extraction/   talks to the radar. Config design, serial control, capture,
                  .dat recording, TLV decoding, integrity auditing.
                  (formerly the standalone `mmwave_direct` package)

    analysis/     never touches hardware. Reads .dat files and produces
                  RT/VT maps, gait metrics and ghost-tagging figures.
                  (formerly `mmwave_analysis`)

    application/  consumes Frames as they arrive and produces a decision --
                  a gesture, a track, a classification -- on the live stream
                  or on a replayed recording, identically.

The split is load-bearing, not cosmetic: anything that can change what is
recorded lives in extraction/, and anything that only interprets what was
already recorded lives in analysis/. That is what makes a recording auditable
after the fact -- analysis can never silently alter the evidence.

Entry points:

    python -m mmwave_suite.extraction.live     record a .dat
    python -m mmwave_suite.extraction.viz      live plots / replay
    python -m mmwave_suite.extraction.audit    .dat integrity check
    python -m mmwave_suite.extraction.design   chirp / config designer
    python -m mmwave_suite.analysis.pipelines.run   analysis + figures
    python -m mmwave_suite.application.gesture_with_machine_learning
                                               live gesture recognition
"""

__version__ = "1.0.0"
