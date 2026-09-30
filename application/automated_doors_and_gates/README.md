# Automated doors and gates

*Placeholder.* Nothing is implemented here yet. This file records what the TI
example is, whether the IWR6843AOP can run it, and what porting it into the
suite would cost — so the decision is a reading decision, not an
investigation.

The working example of how one of these gets ported is
[`../gesture_with_machine_learning/`](../gesture_with_machine_learning/).

| | |
|---|---|
| TI example | `radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/Automated_Doors_And_Gates/` |
| Runs on IWR6843AOP | **Yes.** `automated_doors_68xx_demo_aop.bin`. |
| Prebuilt binaries | `automated_doors_68xx_demo_aop.bin`, `..._isk.bin`, `..._ods.bin` |
| Needs reflashing | Yes. |

## What it is

Approach detection with direction-of-travel discrimination, tuned so a person
walking past does not open the door and a person walking toward it does. A
zone-and-tracker application with a latency budget.

## Relevance

Low for the thesis. Listed because it ships an AOP binary and because it is a
clean, small example of the decision layer that sits on top of a tracker —
worth reading if you want a model for how to turn track state into a discrete
output, which is the same shape as the ghost/human decision.

## Cost

Same infrastructure as People_Tracking and Area_Scanner.
