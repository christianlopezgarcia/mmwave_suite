# Area scanner (zone occupancy)

*Placeholder.* Nothing is implemented here yet. This file records what the TI
example is, whether the IWR6843AOP can run it, and what porting it into the
suite would cost — so the decision is a reading decision, not an
investigation.

The working example of how one of these gets ported is
[`../gesture_with_machine_learning/`](../gesture_with_machine_learning/).

| | |
|---|---|
| TI example | `radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/Area_Scanner/` |
| Runs on IWR6843AOP | **Yes.** `area_scanner_68xx_demo_aop.bin` is built for this module. |
| Prebuilt binaries | `area_scanner_68xx_demo_aop.bin`, `..._isk.bin`, `..._ods.bin` |
| Needs reflashing | Yes. Zone logic and the tracker run on-chip. |

## What it is

Divides the field of view into user-defined zones and reports occupancy per
zone, on top of the same GTRACK tracker as People_Tracking. Intended for
security and industrial safety.

## Relevance

Moderate, and mostly as a tool rather than a subject. A ghost appears in a
zone where nothing is standing, so zone occupancy is a compact,
human-readable way to *show* a ghost to someone who does not read
range-Doppler maps — useful for the thesis write-up and for demonstrations,
less so as evidence.

The zone definitions are also a cheap way to encode the panel-intervention
geometry: put a zone where the ghost is predicted to appear and one where the
person actually is, and the occupancy trace over a walk is the experiment's
result in one plot.

## Cost

Low, if People_Tracking is already ported — it is the same tracker with a
zone layer. Not worth doing first.
