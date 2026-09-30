# application/

The third leg of the suite.

- **`extraction/`** gets bytes off the EVM and turns them into `Frame`s.
- **`analysis/`** studies recorded `Frame`s offline.
- **`application/`** consumes `Frame`s *as they arrive* and produces a
  decision — a gesture, a track, a classification — and can be replayed
  against a recording to get identical results.

## The contract

One interface, in [`common/app.py`](common/app.py):

```python
app.reset()
for frame in stream:            # live or a replayed .dat
    result = app.on_frame(frame)
```

Two drivers feed it:

```python
run_offline(app, "runs/run_x/run_x.dat", "runs/run_x/run_x.cfg")
run_live(app, cfg="...cfg", cli="COM4", data="COM5", out="./runs")
```

`run_live` hands the module to `LiveCapture(on_frame=...)`, which already
exists in `extraction/live.py` — nothing new was needed there. It records the
raw stream while it runs, so **every live session is replayable by
construction**, and a result reproduced from a recording is the result the
sensor produced. That property is the reason for the shared interface; without
it, "it worked on the bench" is not a claim you can check later.

`run_live(send_config=False)` covers firmware that configures itself and has
no chirp CLI — TI's gesture binary is the case that forced it.

Applications run on the parser thread by default. They are cheap (the gesture
pipeline is a 90-element dot product per frame). If one ever gets expensive
enough to threaten the reader, move it to `cap.frame_queue`; the `.dat` is
written *before* parsing, so a slow consumer can never damage the recording.

## What is implemented

### [`gesture_with_machine_learning/`](gesture_with_machine_learning/)

TI's Gesture-with-Machine-Learning lab, ported. Four modes — read TI's
firmware directly, re-run our copy of its network to verify the port, or
compute features on the host from the out-of-box point cloud or heat map.
Includes TI's trained weights extracted from their C headers, the exact
feature maths, a retraining path in pure numpy, three configs, and a range
budget that answers how far each config can classify each gesture class.

Read its [README](gesture_with_machine_learning/README.md) for the analysis of
what transfers off-chip and what does not; the short version is that the
callback route works but TI's weights are only valid on TI's firmware, for
reasons in [ALGORITHM.md](gesture_with_machine_learning/ALGORITHM.md) §2.

## What is surveyed but not implemented

Every other directory here is a placeholder holding one README: what the TI
example is, whether the IWR6843AOP can run it, what porting it would cost, and
whether it is relevant to the ghost-versus-human work. They exist so the next
decision about any of them is a reading decision, not an investigation.

Ranked by what they would actually buy this project:

| | why |
|---|---|
| [`people_tracking/`](people_tracking/) | **Highest value.** GTRACK is the closest thing TI ships to the ghost problem: a ghost either forms a persistent track or fails to associate, and either way the tracker's state is evidence the point cloud does not carry. The offline-port route works on recordings that already exist. |
| [`vital_signs/`](vital_signs/) | The only example with a binary built for **this exact module**. Tests a sharp hypothesis: does a ghost carry its parent's breathing modulation, and if so how much weaker and broader? |
| [`human_non_human_classification/`](human_non_human_classification/) | Same question shape as the thesis, one level up. No firmware to port — it is a method to read and implement over the suite's own maps. |
| [`level_sensing/`](level_sensing/) | Small and concrete: phase-interpolated range, good to a fraction of a bin, which is what the mirror-geometry ghost test needs and currently lacks. No flashing. |
| [`long_range_people_detection/`](long_range_people_detection/) | Worth reading, not porting. Through-wall at 3 m is a low-SNR problem with the same levers as long range; this is where TI writes down how to spend the budget. |
| [`area_scanner/`](area_scanner/) | Zone occupancy on top of GTRACK. Good for *showing* a ghost; weak as evidence. |
| [`automated_doors_and_gates/`](automated_doors_and_gates/) | Clean example of turning track state into a discrete decision. |
| [`parking_garage_sensor/`](parking_garage_sensor/) | Low value, one footnote: it is a strong-multipath application, so its users guide is the likeliest place in the toolbox to find TI's own ghost-rejection heuristics. |
| [`robotics/`](robotics/), [`traffic_monitoring/`](traffic_monitoring/), [`true_ground_speed/`](true_ground_speed/) | Listed so nobody re-investigates them. |

Not given directories. These ship only `.appimage` builds for the xWRL1432 /
xWRL6432 low-power family, which is different silicon — the IWR6843AOP cannot
run them: `1D_Sensing`, `Bike_Radar`, `Motion_and_Presence_Detection`,
`Pose_And_Fall_Detection`, `Sleep_Monitoring`, `Smart_Toilet`,
`Video_Doorbell`. `Machine_Learning_Examples` and `Overhead_Lighting` are
documentation only — an overview page and a users guide, with no source and no
binaries in the toolbox at all.

`Gesture_Recognition`'s own xWRLx432 variant is in the same
category, and its configs are kept under
`gesture_with_machine_learning/ti_reference/xWRLx432_chirp_configs/` because
they are evidence for the range argument.

## Adding one

Follow `gesture_with_machine_learning/`:

1. Copy TI's example verbatim into `<app>/ti_reference/`, unedited, and write
   a README naming exactly what was copied and from which toolbox version.
   Line references into it are then stable and checkable.
2. Read the C before the users guide. For the gesture lab, four of the six
   documented feature descriptions were wrong or incomplete about what the
   code does, and one undocumented difference — the detection matrix being
   linear and single-antenna — decides whether the whole thing ports.
3. Write the algorithm down (`ALGORITHM.md`) with line references, then
   implement against that, not against the prose.
4. Subclass `AppModule`. Keep the front end (features) separable from the back
   end (model, post-processing) so different data sources can be compared on
   the same downstream code.
5. Ship a self-test that runs with no EVM, and be explicit in it about what it
   cannot prove. `gesture_with_machine_learning/tools/selftest.py` validates
   32 things and states plainly that bit-equivalence with TI's C is not one of
   them.
