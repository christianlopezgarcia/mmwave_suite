# The on-chip algorithm, line by line

Everything below is read from `ti_reference/src/6443/`. Line references are to
those files as shipped in Radar Toolbox 4.00.00.05. Where this suite's Python
implements a step, the module is named.

The reason to write this down: TI's users guide describes the features in one
sentence each, and four of the six descriptions are wrong or incomplete about
what the code does. The differences matter — they are the difference between a
port that reproduces the board and one that is merely inspired by it.

---

## 1. Configuration — compiled in, not sent

`cli.c:50` defines `USE_HARD_CODED_CONFIG`. `cli.c:85-115` holds the command
list, and `CLI_task` (`cli.c:215`) feeds it to the CLI parser one line at a
time at boot, then falls through to reading the UART. So the firmware does
accept commands afterwards — but **only the ones `mmw_cli.c:1185-1258`
registers**: `sensorStart`, `sensorStop`, `guiMonitor`, `cfarCfg`,
`multiObjBeamForming`, `calibDcRangeSig`, `clutterRemoval`, `adcbufCfg`,
`compRangeBiasAndRxChanPhase`, `measureRangeBiasAndRxChanPhase`, `aoaFovCfg`,
`cfarFovCfg`, `extendedMaxVelocity`, `CQRxSatMonitor`, `CQSigImgMonitor`,
`analogMonitor`, `lvdsStreamCfg`. Plus `version` via the mmWave extension
(`mmw_cli.c:1179`).

There is **no `profileCfg`, `frameCfg`, `chirpCfg` or `channelCfg`**. The chirp
geometry cannot be changed at runtime. `queryDemoStatus` is also absent, which
is why `RadarLink.handshake` logs a warning and continues — harmless, and
handled.

Transcribed verbatim into `cfg/xwr68xx_AOP_gesture_ti-onchip_28fps.cfg`.

| | |
|---|---|
| `channelCfg 15 1 0` | 4 RX, **1 TX** → 4 virtual antennas, 2×2 |
| `profileCfg 0 60.50001 166 6 34 22 0 102.908 0 64 2350 0 0 42` | 64 ADC samples at 2350 ksps = 27.23 µs; slope 102.908 MHz/µs → **2803 MHz** swept |
| | → 5.35 cm range bins, 64 of them, 3.43 m unambiguous |
| | idle 166 + ramp 34 = **200 µs** chirp |
| `frameCfg 0 0 128 0 35 1 0` | 128 chirps, **35 ms** frame → 28.6 fps, 73% duty |
| | → 128 Doppler bins, 0.095 m/s, ±6.05 m/s |
| `guiMonitor -1 0 0 0 0 0 1` | every standard TLV off; only stats |
| `lowPower 0 1` | ADC low-power mode |

Note `hpfCornerFreq1 = 0` → 175 kHz, which at this slope is a corner at
**25.5 cm**. The near half of the working range sits below the IF high-pass
corner and is attenuated. With ~85 dB of SNR that does not prevent detection,
but it biases `weightedRange` outward and it is baked into the trained model.

`cfarCfg`, `aoaFovCfg` and `cfarFovCfg` are accepted and then never used —
CFAR and AoA are `#if 0`-ed out of the DPC (`objectdetection.c:889`).

---

## 2. The detection matrix is not the out-of-box detection matrix

This is the single most important difference, and nothing in the users guide
mentions it.

| | gesture firmware | stock SDK 3.6 OOB |
|---|---|---|
| magnitude | `HWA_FFT_MODE_MAGNITUDE_ONLY_ENABLED` — **linear** (`dopplerprochwa.c:300`) | `..._MAGNITUDE_LOG2_ENABLED` — **log2** (`dopplerprochwa.c:197`) |
| width | `uint32` (`:291`, `:664`) | `uint16` (`:171`, `:443`) |
| antennas | **TX1-RX1 only** — "Only accumulate on TX1-RX1 for the detection matrix" (`:324`) | summed over all virtual antennas |

So `THRESH_NUM_POINTS = 2000` is a threshold on the linear magnitude of one
virtual channel. There is no scale factor that turns a 12-antenna log-sum into
that. This is why `model/ti_ann_6843.npz` is only valid on TI's firmware.

---

## 3. Per frame

Called from the Doppler DPU after the 2D FFT completes
(`dopplerprochwa.c:1311-1443`).

### 3.1 Suppress slow Doppler — `Computefeatures_preStart`, `gesture.c:206`

Zeroes Doppler bins `j <= 5` and `j >= N-5`, i.e. 11 of 128. At 0.0946 m/s per
bin that discards **everything slower than 0.47 m/s**. Static clutter, yes —
but also the slow part of a gentle swipe, which is why the demo wants a brisk
gesture.

→ `features.suppress_zero_doppler`

### 3.2 Range/Doppler moments — `Computefeatures_RDIBased`, `gesture.c:242`

Over range bins **`[1, 8)`** — bin 0 is excluded from the moments but *is*
searched for the angle peak in 3.3:

```
wtSum      = Σ det[r][d]
weightedRange   = Σ det·r / wtSum
weightedDoppler = Σ det·w[d] / wtSum          w[] = signed bin index
weightedDopplerPos = Σ_{d<N/2} det·w[d] / Σ_{d<N/2} det
weightedDopplerNeg = Σ_{d>=N/2} det·w[d] / Σ_{d>=N/2} det
numDetections   = |{cells > 2000}|
instEnergy      = wtSum / 10000
```

`w[]` is a signed FFT-order index: `0..63` then `-64..-1` (`gesture.c:64-191`,
spelled out as a 128-entry table). "Positive" Doppler is the raw lower half,
so it includes DC — harmless only because DC was just zeroed.

→ `features.rdi_features`, `features.doppler_weights`

### 3.3 Angle moments — ten passes, `dopplerprochwa.c:1311-1440`

Repeat `NUM_SORTED_VALUES = 10` times:

1. `Computefeatures_DOABased` (`gesture.c:319`) scans range bins `[0, 8)` × all
   Doppler for the global maximum, and remembers its magnitude as the weight.
2. Take the four virtual-antenna samples of the 2D-FFT cube at that cell.
   For the AOP (`gesture.c:430-495`): reorder to `[RX4, RX3, RX2, RX1]`, negate
   indices 0 and 2 (two RX feeds enter the patch from the opposite side, so
   their virtual channels are 180° out), scale by 1/64, and pad to 8 with zeros
   for the absent TX2 — the HWA can zero-pad but not zero-fill.
3. Two chained HWA FFTs (`dopplerprochwa.c:509-620`):
   - four 2-point FFTs over adjacent pairs, each zero-padded to 32;
   - then, for each of those 32 bins, one 4-point FFT across the pair results,
     zero-padded to 32, magnitude only.

   Output is 32×32 `uint32`, written with `dstAIdx = 32 words`, `dstBIdx = 1`.
4. `ComputeAngleStats` (`gesture.c:497`) scans that array linearly and wraps
   indices above 15 to negative.
5. Accumulate `Σ w·az`, `Σ w·el`, `Σ w·az²`, `Σ w·el²`, `Σ w` — where `w` is
   the **detection-matrix** magnitude of the cell, not the angle-FFT magnitude.
6. **Zero the cell** so the next pass finds the next-strongest.

Then

```
azMean = Σw·az / Σw            azDispersion = Σw·az²/Σw − azMean²
elMean = Σw·el / Σw            elDispersion = Σw·el²/Σw − elMean²
```

Despite being named `pWtaz_std`, the dispersion is a **variance**
(`dopplerprochwa.c:1423`).

Two things worth noticing. First, this is a **fixed-count detector**: exactly
ten cells contribute every frame whether the scene is busy or empty, so the
feature vector has constant statistical weight. CFAR does not have that
property, and it is a large part of why this works better than a CFAR front end
at this range. Second, the axis labelling is ambiguous: `ComputeAngleStats`
calls `i/32` the azimuth index, but with `dstAIdx = 32` words that index is the
*second* FFT (across pairs). TI modified the paramset "so the angle matrix
matches the MATLAB model" and commented out the swap that used to be here
(`dopplerprochwa.c:1417-1423`). One left-swipe capture settles it empirically.

→ `features.aop_doa_input`, `features.angle_fft_2d`,
`features.angle_features_from_cube`

### 3.4 Azimuth/Doppler correlation — `Computefeatures_Hybrid`, `gesture.c:540`

Pearson correlation of the last **20** frames of `azMean` against
`weightedDoppler`. Reported as 0 until 20 frames have accumulated
(`dopplerprochwa.c:1438`). The C omits the `1/N` on both the cross term and
each sigma, which cancels, and adds `eps = 1e-16` to guard a static buffer.

This is the feature that separates a **twirl** — azimuth and Doppler in
quadrature over a circle, so the correlation averages toward zero — from a
**swipe**, where they are locked in phase and the correlation is strongly
signed. It is also the only feature with memory longer than the ANN window.

→ `features.FeatureWindow.correlation`

---

## 4. The network — `objectdetection.c:932-962`, `ann_utils.c:119`

Six features are normalised **on entry** and shifted into a 15-frame window:

```
gFeatureVector[-6] = (weightedDoppler   − mean[0]) / std[0]
gFeatureVector[-5] = (azMean            − mean[1]) / std[1]
gFeatureVector[-4] = (elMean            − mean[2]) / std[2]
gFeatureVector[-3] = (numDetections     − mean[3]) / std[3]
gFeatureVector[-2] = (weightedRange     − mean[4]) / std[4]
gFeatureVector[-1] = (azDopplerCorr     − mean[5]) / std[5]
```

**That order is not the order of the 10-float TLV**, and it is not the order
the users guide lists the features in. Getting it wrong produces a model that
is ~20% accurate and looks like a training problem. It is asserted in
`ann.py:ANN_FEATURE_ORDER` and checked against `model/ti_ann_6843.json` on
load, and `tools/selftest.py` verifies that reversing it is detectable.

Then 90 → 30 (ReLU) → 60 (ReLU) → 10 (max-subtracted softmax). The four
features in the TLV that the network never sees are `weightedDopplerPos`,
`weightedDopplerNeg`, `azDispersion`, `elDispersion` — recorded anyway, and
available to a retrained model.

→ `ann.GestureANN`

---

## 5. Debounce — `FindGesture`, `gesture.c:575`

A class votes in a frame if its probability clears a per-class threshold; it
fires if it voted in **more than** a per-class count of the last 15 frames.

| class | p threshold (firmware / visualizer) | count of 15 |
|---|---|---|
| no_gesture | 0.6 / **0.99** | 4 |
| l2r, r2l, u2d, d2u | 0.6 | 4 |
| twirl_cw, twirl_ccw | **0.9** | **9** |
| off_push, on_pull | 0.6 | 4 |
| shine | **0.99** | **8** |

The scan runs low index to high and keeps the **last** class that passes, so a
higher-numbered class wins ties. Reproduced as-is; an argmax would be tidier
and would not match the board.

TI's own host visualizer re-implements this from TLV 1051 and raises the
no-gesture threshold from 0.6 to 0.99
(`ti_reference/visualizer/gesture_recognition.py:95`), which stops the idle
class latching over a gesture that is still ramping. That is the default here.

At 28.6 fps the 15-frame window is 525 ms, so the debouncer alone puts a ~0.5 s
floor on back-to-back gestures.

→ `postproc.GestureDebouncer`

---

## 6. Output — `main.c:1689-1745`

Two TLVs per frame, no sub-header, each a bare `float32[10]`:

- **1050** `MMWDEMO_OUTPUT_MSG_GESTURE_FEATURES` — the first ten members of
  `Features_t` in declaration order (`gesture.h:145-154`):
  `weightedDoppler, weightedPositiveDoppler, weightedNegativeDoppler,
  weightedRange, numDetections, weightedAzimuthMean, weightedElevationMean,
  azimuthDopplerCorrelation, weightedAzimuthDispersion,
  weightedElevationDispersion`
- **1051** `MMWDEMO_OUTPUT_MSG_ANN_OP_PROB` — `ANN_struct_t.prob`

Plus the standard stats TLV if enabled. 128 bytes per frame after 32-byte
padding — 4% of a 921600-baud link at 28.6 fps.

The detected gesture itself is **not** in the stream; `FindGesture` writes it
to the **CLI** port with `CLI_write` (`gesture.c:620,632`). A host that only reads
the data port must re-run the debounce itself, which is what `--mode onchip`
does.

→ `extraction/tlv.py:TLVType.GESTURE_FEATURES`, `features.TLV_FEATURE_ORDER`
