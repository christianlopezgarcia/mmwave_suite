# Level sensing (high-accuracy range)

*Placeholder.* Nothing is implemented here yet. This file records what the TI
example is, whether the IWR6843AOP can run it, and what porting it into the
suite would cost — so the decision is a reading decision, not an
investigation.

The working example of how one of these gets ported is
[`../gesture_with_machine_learning/`](../gesture_with_machine_learning/).

| | |
|---|---|
| TI example | `radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/Level_Sensing/` |
| Runs on IWR6843AOP | **Yes**, in the sense that `high_accuracy_68xx_demo.bin` runs; the accuracy claim assumes a directional antenna aimed at a single flat target. |
| Prebuilt binaries | `high_accuracy_68xx_demo.bin` plus 14xx/16xx/L-series variants |
| Needs reflashing | Yes. |

## What it is

Sub-millimetre range estimation of a single dominant target by interpolating
the phase of the range-FFT peak rather than its magnitude.

## Relevance

Small but specific: the *technique* is the right one for measuring how far a
ghost sits behind its parent. Range-bin resolution here is 4-9 cm, which is
too coarse to test a mirror-geometry prediction precisely; phase-based
interpolation on the same data is good to a fraction of a bin.

`analysis/ghost_sections/` method 4 (mirror geometry) is exactly the place
this would be used — the prediction is a specific range offset, and the
current tooling can only check it to the nearest bin.

## Cost

No flashing needed. This is an algorithm to implement in
`analysis/`, over the range profile (TLV 2) or the point cloud, not a
firmware port. Half a day, and it directly sharpens an existing method.
