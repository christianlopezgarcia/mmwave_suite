"""mmwave_suite.application -- end-to-end radar applications.

`extraction` gets bytes off the EVM and turns them into Frames.  `analysis`
studies recorded Frames offline.  This package is the third leg: something
that consumes Frames *as they arrive* and produces a decision -- a gesture, a
presence flag, a classification -- in real time, and can be replayed against a
recording to get bit-identical results.

Every application here follows the same contract (see `common.app`):

    module = SomeApp(...)
    module.reset()
    for frame in stream:            # live or a replayed .dat
        result = module.on_frame(frame)

so the same object drives `extraction.live.LiveCapture(on_frame=...)` and an
offline `extraction.tlv.parse_dat()` loop without knowing which it is.  That
is the whole point: you develop against a recording, then run the identical
code on the sensor.

Applications
------------
gesture_with_machine_learning
    Port of TI's Gesture-with-Machine-Learning lab (radar_toolbox 4.00.00.05)
    to the host, including TI's trained network, plus host-side variants that
    run on stock out-of-box firmware.  See its README for what transfers and
    what does not.

The remaining sub-directories are placeholders carrying a survey of the TI
Radar Toolbox example that would seed them; each README states whether the
IWR6843AOP can run it and what it would cost.
"""
