# Parking garage sensor

*Placeholder.* Nothing is implemented here yet. This file records what the TI
example is, whether the IWR6843AOP can run it, and what porting it into the
suite would cost — so the decision is a reading decision, not an
investigation.

The working example of how one of these gets ported is
[`../gesture_with_machine_learning/`](../gesture_with_machine_learning/).

| | |
|---|---|
| TI example | `radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/Parking_Garage_Sensor/` |
| Runs on IWR6843AOP | Runs; designed for a ceiling mount over parking bays. |
| Prebuilt binaries | `parking_garage_68xx_demo.bin` |
| Needs reflashing | Yes. |

## Relevance

Low. Occupancy of fixed zones from a ceiling mount. Overlaps Area_Scanner.

One footnote worth keeping: a parking garage is a strong-multipath
environment (concrete, metal, parallel surfaces), so if TI documents any
ghost-rejection heuristics anywhere in this toolbox, the users guide here is a
plausible place to find them. Worth ten minutes of reading for that alone.
