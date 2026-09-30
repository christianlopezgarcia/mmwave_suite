# Human / non-human classification

*Placeholder.* Nothing is implemented here yet. This file records what the TI
example is, whether the IWR6843AOP can run it, and what porting it into the
suite would cost — so the decision is a reading decision, not an
investigation.

The working example of how one of these gets ported is
[`../gesture_with_machine_learning/`](../gesture_with_machine_learning/).

| | |
|---|---|
| TI example | `radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/human_non-human_classification/` |
| Runs on IWR6843AOP | **No binary.** The example targets the xWRL6432 low-power part. The method transfers; the firmware does not. |
| Prebuilt binaries | none for 68xx |
| Needs reflashing | N/A |

## Relevance

Conceptually the nearest neighbour to the thesis question, one level up: it
classifies a detected target as human or not from its micro-Doppler
signature. The ghost problem is the same shape — "this detection looks like a
person; is it one?" — with a different negative class.

What is reusable without any TI firmware:

- The **feature set**. Micro-Doppler spread, periodicity, and the ratio of
  limb to torso energy are computed from a range-Doppler or velocity-time map,
  which `analysis/micro_doppler/` already produces from existing recordings.
- The **framing**: a per-track binary classifier with an explicit negative
  class, rather than a threshold on one statistic.

What is not: the firmware, the trained model, and the low-power chirp design,
all of which are xWRL6432-specific.

## Cost

There is nothing to flash and nothing to port. This is a *method* to read
(`docs/human_non-human_classification_user_guide.html`) and then implement
against the suite's own maps. The blocker is the same one in
`mmwave-open-items`: the gait metrics are not yet trustworthy, and a
classifier built on untrustworthy features inherits the problem.
