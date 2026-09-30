# Vital signs (breathing and heart rate)

*Placeholder.* Nothing is implemented here yet. This file records what the TI
example is, whether the IWR6843AOP can run it, and what porting it into the
suite would cost — so the decision is a reading decision, not an
investigation.

The working example of how one of these gets ported is
[`../gesture_with_machine_learning/`](../gesture_with_machine_learning/).

| | |
|---|---|
| TI example | `radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/Vital_Signs/` |
| Runs on IWR6843AOP | **Yes — and this is the only example here with a binary built specifically for the AOP module.** |
| Prebuilt binaries | `vital_signs_tracking_6843AOP_demo.bin`, `vital_signs_tracking_6843ISK_demo.bin` |
| Needs reflashing | Yes. Phase-based vital-signs extraction needs the per-chirp phase of a tracked range bin, which no out-of-box TLV carries. |

## The interesting question for the thesis

A multipath ghost is a delayed, attenuated copy of a real return, so on the
face of it it carries the *same* chest-wall phase modulation as the person who
produced it — breathing and all. If that is true, vital signs are useless as a
ghost discriminator and it is worth knowing early.

But there are reasons it might not hold, and each is testable:

- The reflecting surface adds its own phase noise. A plasterboard wall moves;
  a plywood panel resonates. That should broaden the breathing peak of a ghost
  relative to its parent.
- The ghost's SNR is 10-20 dB down, and chest-wall displacement is ~1 mm =
  λ/5 at 60 GHz. Breathing may simply not clear the phase-noise floor on the
  ghost even when it is obvious on the person.
- A ghost formed off a *moving* limb rather than the torso has no periodic
  component at all.

So the hypothesis worth testing is not "ghosts do not breathe" but "a ghost's
breathing peak is weaker and broader than its parent's, by a margin that
scales with the reflector". That is a one-session experiment with the panel
setup already described in `GHOST_VALIDATION_PLAN.txt`.

## Cost

Flashing is cheap and reversible. The demo outputs its own vital-signs TLV,
so the parser needs one new decoder. The hard part is that the demo tracks
ONE subject in a gated range window — reading a ghost and its parent
simultaneously is not what it is built for, and may need two runs with the
range gate moved, or a host-side reimplementation over the raw phase.
