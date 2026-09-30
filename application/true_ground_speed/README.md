# True ground speed

*Placeholder.* Nothing is implemented here yet. This file records what the TI
example is, whether the IWR6843AOP can run it, and what porting it into the
suite would cost — so the decision is a reading decision, not an
investigation.

The working example of how one of these gets ported is
[`../gesture_with_machine_learning/`](../gesture_with_machine_learning/).

| | |
|---|---|
| TI example | `radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/True_Ground_Speed/` |
| Runs on IWR6843AOP | Runs; designed for a downward-tilted mount on a moving vehicle. |
| Prebuilt binaries | `true_ground_speed_68xx_demo.bin` |
| Needs reflashing | Yes. |

## Relevance

None directly. Kept in the survey so the index is complete and nobody
re-investigates it.

The one transferable idea: it estimates platform motion by fitting a
cosine model to the Doppler-versus-azimuth distribution of static clutter.
That fit is a clean way to identify which detections belong to the static
scene, which is the complement of the problem `analysis/core/clutter.py`
solves for a stationary radar.
