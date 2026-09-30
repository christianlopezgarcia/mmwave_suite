# Long-range people detection

*Placeholder.* Nothing is implemented here yet. This file records what the TI
example is, whether the IWR6843AOP can run it, and what porting it into the
suite would cost — so the decision is a reading decision, not an
investigation.

The working example of how one of these gets ported is
[`../gesture_with_machine_learning/`](../gesture_with_machine_learning/).

| | |
|---|---|
| TI example | `radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/Long_Range_People_Detection/` |
| Runs on IWR6843AOP | **Partly.** `long_range_people_det_6843_demo.bin` targets the ISK's higher-gain antenna. The AOP's ~5.5 dBi patch gives up roughly 7 dB of two-way gain against the ISK's ~9 dBi, so the range claim does not transfer. |
| Prebuilt binaries | `long_range_people_det_6843_demo.bin`, `..._6443_demo.bin` |
| Needs reflashing | Yes. |

## What it is

A chirp design and detection chain tuned for detecting a person at tens of
metres: long ramps, narrow bandwidth, heavy integration, aggressive CFAR.

## Relevance

Indirect but real. The thesis works at a few metres through a wall, which is
a *low-SNR* problem even though it is not a long-range one — and the two share
the same levers: integration gain, CFAR threshold setting, and what happens to
angle estimates when SNR is marginal. The chirp-design reasoning in the users
guide is worth reading for that, independently of the firmware.

Concretely: through-wall attenuation at 60 GHz is severe (plasterboard alone
is typically 10-20 dB two-way), so the link budget through a wall at 3 m can
resemble free space at 15 m. This example is where TI writes down how they
spend the budget.

## Cost

Reading it is free and probably worthwhile. Porting it is not — the design is
tied to an antenna this module does not have.
