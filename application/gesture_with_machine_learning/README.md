# Gesture recognition with machine learning — IWR6843AOP

TI's `Gesture_with_Machine_Learning` lab (Radar Toolbox 4.00.00.05), ported to
this suite, plus the analysis of what can and cannot be reproduced without
flashing TI's binary.

Everything here runs from `extraction.live`'s existing frame callback. Nothing
in `extraction/` or `analysis/` was restructured; the one change made there was
teaching `extraction/tlv.py` TI's two application TLV codes (1050, 1051) so a
gesture capture does not log two `unknown_tlv_type` anomalies per frame.

---

## The short answer to "can I skip the flash?"

**Partly, and the part you lose is the part that makes the demo work at 0.3 m.**

Three things are true at once, and they pull in different directions:

1. **The callback idea is right.** `LiveCapture(on_frame=...)` already exists and
   is the correct hook. `application/common/app.py` formalises it, and the
   gesture pipeline runs on it at ~30 µs per frame. This was never the hard part.

2. **TI's trained network cannot be fed from out-of-box firmware.** Not "needs
   tweaking" — structurally cannot. The gesture firmware ships a *modified*
   Doppler DPU: its detection matrix is **linear** magnitude from **TX1‑RX1 only**
   (`dopplerprochwa.c:300`, `:324`), while the stock SDK writes **log2** magnitude
   **summed over all twelve virtual antennas** (`SDK 3.6 dopplerprochwa.c:197`).
   Every magnitude-weighted feature therefore lands on a different scale, and
   `numDetections` thresholds at 2000 on a quantity that no longer exists.
   A log-sum over 12 antennas is not an invertible function of a linear
   magnitude on 1, so no correction factor exists. The shipped weights are
   valid on TI's firmware and nowhere else.

3. **The two angle features are not on the wire at all in out-of-box mode.**
   They need the per-antenna 2D-FFT cube at the *peak Doppler* cell. TLV 4 and
   TLV 8 are zero-Doppler only — and the gesture algorithm explicitly zeroes
   ±5 Doppler bins first, so the zero-Doppler heat maps are exactly the data
   it throws away. The point cloud (TLV 1 + 7) is the only out-of-box source
   of non-zero-Doppler angle.

So the honest decomposition is:

| Want | Flash TI's binary? | Works today |
|---|---|---|
| TI's nine gestures at 0.3 m, as demonstrated | **yes** | `--mode onchip` |
| Same, with your own thresholds / logging / UI | **yes** | `--mode onchip` |
| Verify our Python matches the board | **yes** | `--mode onchip_features` |
| Your own gesture set, your own distance | no | `--mode points` + retrain |
| TI's exact algorithm, off-chip | no, but needs raw ADC (DCA1000) | `features.py` primitives |

Flashing is reversible — the out-of-box binary goes back on in two minutes with
UniFlash — and `--mode onchip` turns this suite into the visualizer, which is
almost certainly what you actually want first. Do that, capture the features,
*then* decide whether the no-flash route is worth the retraining.

---

## How TI does it

Full transcription in **[ALGORITHM.md](ALGORITHM.md)**. In one page:

The chirp config is compiled into the firmware (`cli.c:85-115`,
`USE_HARD_CODED_CONFIG`) and replayed at boot — which is why the visualizer says
"Start **without** Send Configuration". 1 TX, 4 RX, 64 range bins at 5.35 cm,
128 Doppler bins at 0.095 m/s, 35 ms frames = 28.6 fps.

Per frame, on the R4F:

1. Zero Doppler bins 0–5 and 123–127. That discards everything slower than
   **0.47 m/s** — static clutter, but also a gentle swipe.
2. Over range bins 1–7 (**5.4 cm to 37.5 cm**), compute magnitude-weighted
   Doppler, +ve Doppler, −ve Doppler, range, and a count of cells above a
   threshold.
3. Ten times: find the strongest remaining cell in range bins 0–7, angle-FFT
   that one cell (32×32, via two chained HWA FFTs), record its peak angle bin
   weighted by the cell's magnitude, then **zero the cell** and repeat. This is
   a fixed-count detector, not CFAR — which is quietly why it beats CFAR here:
   the feature vector carries the same statistical weight every frame.
4. Correlate the last 20 frames of weighted azimuth against weighted Doppler.
   That one feature is what separates a twirl (azimuth and Doppler in
   quadrature → near-zero correlation) from a swipe (locked in phase →
   strongly signed).
5. Normalise six of these, push into a 15-frame window, and run a
   90→30→60→10 MLP. 4,400 parameters, ~9 kB of weights.
6. Debounce: a class fires only if it clears a per-class probability threshold
   in more than a per-class count of the last 15 frames.

The per-class thresholds are TI telling you which classes their own network is
unreliable on: 0.6 / 4-of-15 for the swipes, 0.9 / 9-of-15 for the twirls,
0.99 / 8-of-15 for shine.

### The model's own confession

`model/ti_ann_6843.npz`, extracted from TI's headers, carries the
normalisation statistics it was fitted with:

| feature | mean | std |
|---|---|---|
| weightedDoppler | −0.419 | 3.284 |
| weightedAzimuthMean | +0.049 | 4.637 |
| weightedElevationMean | −0.756 | 3.458 |
| numDetections | +22.02 | 20.67 |
| **weightedRange** | **+4.386 bins** | **0.667 bins** |
| azimuthDopplerCorr | +0.027 | 0.554 |

At 5.35 cm per bin that is a hand at **23.5 cm ± 3.6 cm**. A hand at 50 cm is
**+7.4σ** on that input. The network was never shown it and there is no reason
to expect anything sensible. This is not a soft limitation you can push on — it
is the dominant term in the first layer.

---

## How far can gesture recognition actually go?

Run `python tools/range_budget.py` for the live numbers. The model and its
assumptions are documented in that file; absolute SNR is good to about ±5 dB,
but the scaling laws and the ratios between configs are solid.

Three ceilings compete, and the lowest wins.

### 1. Detection — never the limit

A hand is enormous this close. Every config here puts a 0.01 m² hand at
**80–90 dB SNR at 20–30 cm**, and still 15 dB at ~12 m. Detection range is not
what stops you. Ignore it.

### 2. Angle — the real ceiling, and it scales as 1/R³

A swipe is recognised by how far the target's angle *centroid* moves. Two
things happen as you back away:

- the angular excursion of a fixed-size gesture shrinks as **1/R**;
- the angle estimate's noise grows as **1/√SNR ∝ R²**.

so the angular signal-to-noise of a gesture falls as **1/R³**. At the SNRs a
hand produces, though, the CRLB term is millidegrees and predicts recognition
at 5 m, which is nonsense. What actually floors angle precision is the residual
RX phase error left after `compRangeBiasAndRxChanPhase` — about 5° of phase,
which across a λ/2 pair is **1.6° of angle**, and no amount of SNR removes it
because it is the same error every frame.

That floor is the one term a bigger azimuth aperture improves: it scales as
1/(N−1), so 2 elements → 1.6°, 4 elements → 0.53°.

**Requiring the gesture's excursion to be ≥10σ:**

| gesture class | what it asks the radar to measure | TI's 1 TX (2-elem az) | 3 TX (4-elem az) |
|---|---|---|---|
| push / pull (on / off) | 20 cm of **range** | >10 m | >10 m |
| swipe L2R / R2L / U2D / D2U | 30 cm of **angle** | **1.1 m** | **3.2 m** |
| twirl CW / CCW | 4 cm of **angle** | **0.14 m** | **0.43 m** |
| shine | micro-Doppler on a fingertip | ~6 m (SNR-limited) | ~7 m |

Two independent checks that this model is right:

- It reproduces TI's own operating point. They train at 23.5 cm, and 23.5 cm
  is inside the twirl ceiling (0.14 m is marginal — hence the 0.9 probability
  threshold and 9-of-15 count on exactly those two classes).
- **TI's own 2 m variant drops exactly the classes this model says die first.**
  `ti_reference/xWRLx432_chirp_configs/` is TI's "Fixed Distance (2 m)" gesture
  demo, and its class list
  (`ti_reference/visualizer/gesture_recognition.py:40`) is
  `[none, L2R, R2L, U2D, D2U, push, pull]` — **no twirl, no shine**. They also
  moved to a 3-RX part for more azimuth aperture and kept the same 2.8 GHz
  bandwidth and the same 28.6 fps. That is the same trade this table predicts,
  made independently by the people who wrote the demo.

Halve these numbers for a real room. The model has no body clutter in it, and
at 2 m your forearm and torso sit behind your hand with ~70× its RCS.

### 3. Link — what forces the point-cloud route

8N1 means 10 bits per byte, so 921600 baud = 92,160 B/s.

| payload | bytes/frame | max fps @ 921600 | max fps @ 3.125 Mbaud |
|---|---|---|---|
| TI's features + probabilities (TLV 1050+1051) | 128 | — (4% at 28.6 fps) | — |
| point cloud + side info, 40 points | 896 | 103 | 349 |
| + range-Doppler heat map, 32×128 | 9,088 | **10.1** | **34.4** |
| + range-Doppler heat map, 64×128 | 17,312 | 5.3 | 18.0 |

A gesture lasts ~0.5 s. At 10 fps you sample a swipe with five points and the
15-frame window spans 1.5 s. That is why the heat-map route is offline-only —
**at 921600 baud.**

**This is the one assumption worth testing before accepting the conclusion.**
921600 is a default, not a limit: the out-of-box demo advertises
`MMWDEMO_DATAUART_MAX_BAUDRATE_SUPPORTED = 3125000` (`extraction/link.py:53`)
and `RadarLink.handshake` already negotiates whatever `data_baud` you ask for.
At 3.125 Mbaud the heat map fits at 34 fps and the TLV-5 route becomes live.
Whether the USB-serial bridge on this EVM sustains it is a property of the
hardware, not the firmware, and the failure mode is silent byte loss rather
than an error. `tools/uart_probe.py` measures it by watching the parser's
resync and lost-byte counters:

```
python tools/uart_probe.py --cfg cfg/xwr68xx_AOP_gesture_rdmap_8fps.cfg \
    --seconds 12 --bauds 921600 1250000 2000000 3125000
```

Run that before writing off TLV 5.

---

## What is here

```
ann.py          90-30-60-10 MLP in numpy. Port of ann_utils.c.
features.py     TI's exact feature maths, plus host-computable stand-ins.
postproc.py     Port of FindGesture() -- per-class threshold + count debounce.
pipeline.py     Frame in, gesture out. Four modes (see below).
dataset.py      Record labelled feature streams; cut them into windows.
train.py        Retrain the same architecture, pure numpy, into the same .npz.
__main__.py     CLI: live / record / replay.

cfg/            Three configs; see cfg/README.md.
model/          TI's weights as .npz + .json, and what they mean.
tools/
  export_ti_weights.py  regenerate model/ from TI's C headers
  range_budget.py       the tables above
  uart_probe.py         find the highest clean data-port baud
  selftest.py           32 checks, no EVM needed
ti_reference/   TI's lab, verbatim. See ti_reference/README.md.
```

### Modes

| mode | firmware | features from | model |
|---|---|---|---|
| `onchip` | TI gesture | board (TLV 1050) | board (TLV 1051) |
| `onchip_features` | TI gesture | board (TLV 1050) | **ours** — proves the port |
| `points` | out-of-box | TLV 1 + 7 | **retrain required** |
| `heatmap` | out-of-box | TLV 5 + TLV 1 | **retrain required** |

Everything downstream of the feature vector — window, normalisation, network,
debouncer — is shared, so comparing modes compares front ends only.

---

## Using it

### Read a board running TI's firmware

Flash `ti_reference/prebuilt_binaries/gesture_ML_6443_AOP.bin` with UniFlash
(SOP2 jumper on, power cycle, flash, jumper off, power cycle). Then:

```
python -m mmwave_suite.application.gesture_with_machine_learning live --mode onchip
```

No config is sent — that firmware has no chirp CLI and would error on every
line (`mmw_cli.c:1185` registers only sensorStart/Stop, guiMonitor, cfar*,
adcbufCfg, fov and monitors). The `.cfg` in `cfg/` is read for TLV geometry and
copied into the run directory for provenance. The raw stream is still recorded,
so every live session is replayable.

### Prove the port matches the board

```
python -m mmwave_suite.application.gesture_with_machine_learning \
    live --mode onchip_features --seconds 30
```

The summary prints `host vs board: max ..., mean ...`. Expect ~1e-6 (float32 on
the R4F versus float64 here). Above ~1e-3 means the feature *order* is wrong —
see `ANN_FEATURE_ORDER` in `ann.py`, which is the order `objectdetection.c:954`
uses and is **not** the order of the 10-float TLV. This is the only check that
validates the port against the actual C; `tools/selftest.py` cannot, because it
generates both sides.

### Stock firmware, your own model

```
# one take per gesture, several takes each
python -m ...gesture_with_machine_learning record --label l2r --seconds 30 \
    --countdown 3 --cfg cfg/xwr68xx_AOP_gesture_points_30fps.cfg

python -m ...gesture_with_machine_learning.train --data data/gestures \
    --out model/my_ann.npz

python -m ...gesture_with_machine_learning live --mode points \
    --model model/my_ann.npz
```

`dataset.windows()` labels a window with its take's class only if it contains
enough motion, so the pauses between repetitions become `no_gesture` examples
rather than mislabelled noise. That ratio (`--motion-quantile`) is the single
biggest lever on false-positive rate.

Judge a retrained model on a take you recorded **separately** and replayed with
`replay` — the training split leaks, because windows within a take overlap.

### Replay anything

```
python -m ...gesture_with_machine_learning replay runs/run_X/run_X.dat --mode points
```

---

## Recommendation

1. **Run `tools/uart_probe.py` first.** Ten minutes, and it decides whether the
   heat-map route exists at all. Everything below assumes 921600.
2. **Flash TI's binary and run `--mode onchip`.** You get the working demo, and
   the suite becomes the host for it — recording, indexing, replay, your own
   thresholds. This is what the lab is for.
3. **Then `--mode onchip_features` for one 30 s take** to confirm the port, and
   keep those features: they are labelled training data for free, produced by
   the front end the weights were fitted to.
4. **Only then decide about no-flash.** `--mode points` is real and it works
   mechanically — a synthetic swipe moves the features exactly as it should,
   and a real 122-frame walking capture replays clean with no false fires — but
   it is a new model on new features, and at 30 fps with a 4-element azimuth
   aperture its natural target is *hand-scale gestures out to about a metre*,
   not TI's finger-scale set at 23 cm.

The one thing not worth doing is trying to make TI's weights eat out-of-box
features. That is the path that looks closest to working and is furthest from it.

---

## Open questions

- **Highest clean data-port baud on this EVM.** Decides whether TLV 5 is live-
  capable. `tools/uart_probe.py`. Unmeasured.
- **Angle-axis labelling.** `ComputeAngleStats` calls the second FFT's index
  "azimuth" while the memory layout suggests it is the first; TI modified the
  paramset "so the angle matrix matches the MATLAB model" and deleted the swap
  (`dopplerprochwa.c:1423`). One left-swipe capture in `onchip` mode settles
  it. Only matters if you rebuild features from a raw cube; it flips two
  columns.
- **Whether `--mode points` classifies real gestures.** Verified mechanically
  (features respond correctly to a scripted swipe, no false fires on real
  recordings). Not verified against real hand gestures — no labelled capture
  exists yet.
- **Whether 6443 binaries run on this specific 6843AOP module.** TI states they
  are interchangeable (the demo uses only the HWA, never the C674x DSP, so the
  6443's missing DSP is irrelevant). Untested here.
