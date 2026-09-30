# Robotics (obstacle detection, ground speed)

*Placeholder.* Nothing is implemented here yet. This file records what the TI
example is, whether the IWR6843AOP can run it, and what porting it into the
suite would cost — so the decision is a reading decision, not an
investigation.

The working example of how one of these gets ported is
[`../gesture_with_machine_learning/`](../gesture_with_machine_learning/).

| | |
|---|---|
| TI example | `radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/Robotics/` |
| Runs on IWR6843AOP | Runs. |
| Prebuilt binaries | two 68xx binaries |
| Needs reflashing | Yes. |

## Relevance

Low for the thesis. Included because the obstacle-detection chain is a good
reference for near-field CFAR tuning — the same regime the gesture
point-cloud config had to solve (antenna coupling in the first range bins,
peak grouping versus spatial spread).
