# People tracking (group tracker)

*Placeholder.* Nothing is implemented here yet. This file records what the TI
example is, whether the IWR6843AOP can run it, and what porting it into the
suite would cost — so the decision is a reading decision, not an
investigation.

The working example of how one of these gets ported is
[`../gesture_with_machine_learning/`](../gesture_with_machine_learning/).

| | |
|---|---|
| TI example | `radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/People_Tracking/` |
| Runs on IWR6843AOP | **Yes.** `3D_people_track_68xx_demo_hcc.bin` and the overhead variant; the AOP/ISK difference is handled in the .cfg, not the binary. |
| Prebuilt binaries | `3D_people_track_6843_demo.bin`, `3D_people_track_68xx_demo_hcc.bin`, `overhead_3d_people_track_demo_default.bin` |
| Needs reflashing | Yes — the group tracker (GTRACK) runs on the DSP and emits TLV types 1010-1012 (target list, target index, presence) that the out-of-box demo does not produce. |

## Why this one matters most for the thesis

This is the closest TI example to the ghost-versus-human problem. GTRACK
maintains Kalman-filtered 3D tracks with an association gate, so a multipath
ghost either (a) forms its own persistent track, which is exactly the failure
mode under study, or (b) fails to associate and is dropped, which is a
detector. Either way the tracker's own state — track age, association
history, gate residuals — is signal that the point cloud alone does not carry.

Two ways to use it, and they answer different questions:

1. **Flash it and record TLV 1010/1012.** Gives TI's tracker output directly.
   Requires teaching `extraction/tlv.py` the target-list TLVs (the same kind of
   change already made for 1050/1051 — about 30 lines) and a
   `pipeline.py`-shaped consumer. Answers: *does a production tracker ghost?*
2. **Port GTRACK's association logic to the host** and run it on the existing
   point-cloud recordings in `repo/runs/`. Slower to build, but it works on
   every capture already taken, including the panel-intervention runs the
   ghost validation plan depends on, and it lets the gating thresholds be
   swept offline. Answers: *what would a tracker have done to these ghosts?*

Option 2 is probably the better use of time: the recordings already exist, and
`GHOST_VALIDATION_PLAN.txt`'s G1/G2/G3 taxonomy is a statement about track
behaviour that currently has no tracker to test it against.

## Cost

Option 1: a day. New TLV decoders, a consumer, a config. Reversible flash.
Option 2: a week. GTRACK is a real algorithm — constant-velocity Kalman,
Mahalanobis gating, allocation/de-allocation state machine. TI ships the C
under `src/`; it is readable but it is not small.
