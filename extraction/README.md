# mmwave_suite.extraction

An offline replacement for the TI mmWave Demo Visualizer data path, plus the
audit evidence behind it. This half of the suite talks to the radar; nothing in
[analysis/](../analysis/) does.

Built for: xWR68xx_AOP, mmWave SDK 3.6, Visualizer 3.6.0.0.
Dependencies: `numpy`, `pyserial`, and `matplotlib` + `scipy` for `viz.py`
(the parser, config and audit tools need only numpy). No network, ever.

**Run every command below from the directory that contains `mmwave_suite/`.**

```bash
python -m mmwave_suite.extraction.live      # record a .dat
python -m mmwave_suite.extraction.viz       # live plots / replay
python -m mmwave_suite.extraction.audit     # .dat integrity check
python -m mmwave_suite.extraction.design    # chirp / config designer
python -m mmwave_suite.extraction.highfidelity.configs   # the capture presets
```

---

## 1. Findings: is the Visualizer hiding data?

Audited against
`C:\ti\guicomposer\runtime\gcruntime.v11\mmWave_Demo_Visualizer\app\*.js`
and verified empirically against 14 of your own `.dat` recordings.

### 1.1 The `.dat` file is raw. No filtering, thresholding, or point dropping.

`mmWave.js:305` — `saveStreamData(data)` is the **first statement** in the data
port handler, before the magic-word check, before framing, before any parse:

```js
onDataReceived: function (data) {
    if (data) {
        if (Params) {
            var numDataFrameAdded = 0;
            // start with saving the data, if user has requested it
            saveStreamData(data);          // <-- raw bytes straight to disk
            ...
```

Empirically confirmed on your data, with one correction to an earlier draft of
this file:

* `numDetectedObj` in the frame header matches the TLV-1 payload length in
  **every frame of every file checked**. This is the load-bearing evidence: if
  the Visualizer were discarding points, these two would disagree. They never do.
* Frame numbers are contiguous in most files, so frames are not being dropped
  before the write either.
* **Byte accounting is not always perfect, and an earlier version of this
  document wrongly claimed it was.** Of the recordings checked, 10 show non-zero
  `bytes_skipped`. Most is a leading partial frame — TI begins writing on
  whatever chunk arrives, so a `.dat` normally starts mid-frame — but three TI
  recordings also show a genuine mid-stream `resync`, i.e. bytes the *host* lost
  during capture.

That host-side loss is a property of the capture, not evidence of filtering: the
point counts still reconcile in every surviving frame. But the correct claim is
"no points are removed between the UART and the file", not "every byte always
arrives". `audit.py` now reports the two separately and only issues the
"verbatim copy" verdict when `resyncs == 0` and no mid-stream bytes are missing.

### 1.2 But the live *display* is lossy — and that loss never reaches the file

| Where | What it does |
|---|---|
| `mmWave.js:271` | A frame is queued for processing only if `onPlotsTab === true && in_process1 === false`. Switch tabs or let the UI fall behind, and frames are dropped from the display. |
| `mmWave.js:317-322` | If the accumulated buffer does not begin with a magic word, the **entire** buffer is discarded (`dataframe = []`). |
| `mmWave.js:2384` | TI maintains a `droppedFrames` counter, so this is known behaviour. |
| `mmWave.js:2389-2391` | Above 100 frames it warns "Performance Degradation seen: Reduce number of plots or decrease frame rate". |

So: **what you saw on screen is not what is in the file.** The file is complete.

### 1.3 The scatter plot is a multi-frame accumulation, not one frame

`mmWave.js:4383-4406` (`scatterDisplayTime`) and `mmWave.js:2689-2706`:

```js
var aggFrames = Math.round(val / framePeriodicity);   // val = slider seconds * 1000
```

The scatter plot shows a sliding window of `aggFrames` frames of accumulated
points (`Params.scatter_data`), with the oldest frame's points spliced off the
front each frame. If you judged ghost density by eye in the Visualizer, you were
looking at up to several seconds of superimposed point clouds. Colour-mapping is
force-disabled whenever `aggFrames > 1` (`mmWave.js:4397-4401`) because intensity
is not accumulated.

### 1.4 Where points *actually* get dropped: on the radar DSP

This is the real answer for ghost/multipath work. From your
`door_metal_1fps.cfg`, every one of these gates runs on-chip, **before** the
UART:

```
cfarCfg  -1 0 2 8 4 3 0 15 1     CFAR range threshold 15 dB, peak grouping ON
cfarCfg  -1 1 0 4 2 3 1 15 1     CFAR doppler threshold 15 dB, peak grouping ON
cfarFovCfg -1 0 0 8.92           range gate   0 .. 8.92 m
cfarFovCfg -1 1 -1 1.00          doppler gate -1 .. +1 m/s
aoaFovCfg  -1 -90 90 -90 90      angle gate   full FoV
multiObjBeamForming -1 1 0.5     ON, threshold 0.5
clutterRemoval -1 0              OFF
```

Two of these matter directly to you:

* **`multiObjBeamForming 1 0.5`** reports *secondary* angular peaks at ≥50 % of
  the dominant peak within the same range-Doppler bin. That mechanism is a
  direct generator of the reported ghost points you are trying to classify.
  Turning it off removes a whole class of ghosts — and also removes genuine
  second targets. It should be an explicit, documented variable in your
  experiments, not a default.
* **`cfarFovCfg -1 1 -1 1.00`** clips Doppler to ±1 m/s on-chip. Anything faster
  is discarded before it can reach you. Your derived `dopplerResolutionMps` is
  0.1217 m/s and max unambiguous velocity ±0.973 m/s, so the gate is
  approximately the full unambiguous range here — but it is a hard gate and it
  is worth stating in the thesis.

Auditing the recording `test4_plywood_triply/..._2026_07_15T00_46_55_906.dat`
against this config shows **653 of 753 points (87 %) have exactly zero Doppler**
— static returns, with `clutterRemoval` disabled. Most of that point cloud is
wall and furniture, not the subject. (The `walking_parallel_shorter_length`
recording, captured with the 10 fps config, shows the same effect at 1262 of
1634 points, 77 %.)

A detail worth knowing: in that recording 48 points sit marginally outside the
declared 8.92 m range gate (max observed 8.939 m). The gate is applied in
range-*index* space, and bin 205 × 0.043602 m = 8.938 m, so this is index
rounding, not a leak.

---

## 2. Findings: defects in TI's reference Python parser

Your pipeline (`dat_parser_plots_v2.py`) uses
`ti_mmw_official_tool/parser_scripts/parser_mmw_demo.py`. It diverges from the
JavaScript the Visualizer actually runs, in three ways.

### 2.1 A frame with zero detections aborts the parse — this is costing you data

`parser_mmw_demo.py:198-200`:

```python
elif numDetObj <= 0:
    result = TC_FAIL
```

and `dat_parser_plots_v2.py:68`:

```python
if result[0] != 0: break
```

A frame with no detections is a perfectly valid frame. Treating it as a parse
failure ends the pass. Your 24-chunk split limits the blast radius to one chunk,
but the loss is still large — and it lands hardest on exactly the recordings you
care about, where a behind-wall subject drops out of detection intermittently:

| File | Frames present | Your pipeline recovers | Lost |
|---|---|---|---|
| `run_2026-04-01_20-20-16.dat` | 82 496 | 82 496 | 0.0 % |
| `..._2026_04_24T00_08_08_752.dat` | 450 | 253 | **43.8 %** |
| `..._2026_04_24T00_03_09_361.dat` | 450 | 297 | **34.0 %** |
| `..._2026_02_19T23_09_08_330.dat` | 424 | 66 | **84.4 %** |
| `..._2026_04_22T00_37_20_944.dat` | 450 | 32 | **92.9 %** |

(`walking_from_point1_to_point6` is the 92.9 % one.) Files with no empty frames
are unaffected, which is why this has gone unnoticed.

There is a second, subtler consequence: because the parse stops rather than
skipping, the `else` branch in `parse_chunk` that appends an empty frame is
unreachable. Empty frames never enter the time series at all, so your time axis
silently omits the intervals where the subject was not detected — which is
precisely the signal a ghost-vs-human classifier needs.

### 2.2 SNR and noise are read as unsigned; they are signed

`parser_mmw_demo.py:287-289` uses `getUint16`. The struct is `int16_t`
(`mmWave.js:3339-3346`), and the JS sign-corrects it:

```js
math.forEach(snrVal, function (value, idx, ary) {
    if (value > 32767) { ary[idx] = ary[idx] - 65536; }
});
var snrDB = math.map(snrVal, function (value) { return value * 0.1; });
```

Any negative SNR or noise becomes ≈ +6553 dB instead of a small negative number.
No negative values appear in the files checked so far, so this has not bitten you
yet — but weak behind-wall returns near the noise floor are exactly where it
would, and it would corrupt an SNR-threshold-based ghost filter without any
obvious symptom.

### 2.3 TLV position is assumed, not dispatched

`parser_mmw_demo.py` requires TLV #1 to be type 1 and TLV #2 to be type 7,
otherwise SNR/noise are filled with zeros (`parser_mmw_demo.py:295-298`). The
Visualizer instead loops over `numTLVs` and dispatches on type
(`mmWave.js:2293-2328`), then deliberately re-orders processing
(`mmWave.js:2330-2357`) because side info must be decoded before the point cloud.

Your observed orderings:

* `1,7,2,6,9` — most files. Safe by luck.
* `1,2,6,9` — `..._2026_04_22T00_37_20_944.dat`, in **403 of its 450 frames**.
  Those 403 carry no type-7 TLV and therefore no SNR or noise at all; the
  remaining 47 do. (An earlier draft of this file claimed the recording had "no
  type-7 TLV at all" and that every SNR in it was invented — that was wrong.
  The correct statement is 403 of 450.)

  The pattern is not random: the frames without side info are exactly the
  frames with zero detections. The demo emits TLV 7 only when there is at least
  one point to describe. So "missing SNR" and "empty frame" are the same event
  seen twice — which is why a parser that treats an empty frame as a failure
  and a parser that assumes TLV#2 is type 7 fail on the same recordings.

---

## 3. Findings: undocumented / app-only protocol details

Things handled in the app that the SDK stream spec does not make clear.

| Detail | Source | Note |
|---|---|---|
| **32-byte packet padding** | verified empirically | `totalPacketLen` is always rounded up to a multiple of 32 (`MMWDEMO_OUTPUT_MSG_SEGMENT_LEN`). 140/150 frames in one file carry 4–28 bytes of padding after the last TLV. The pad is **uninitialised**, not zeroed — one sample was `20 54 00 08`. Two consequences: sum-of-TLV-lengths never equals `totalPacketLen`, and padding can contain a byte sequence that looks like a magic word. Always chain frames through `totalPacketLen`; never rediscover boundaries by scanning. |
| **TLV length excludes its own 8-byte header** | `mmWave.js:2296,2327` | True for SDK 3.x mmw demo, not universal across TI demos. `tlv.py` detects and reports the other convention rather than assuming. |
| **`peakVal` is not transmitted** | `mmWave.js:2672` | The "intensity" colour axis is reconstructed as `snrDB + noiseDB`. There is no peak-value field in the point cloud. |
| **`numRangeBins` = 1022, not 1024** | `input_validations.js:296-308` | Jira MMWSDK-1627. With 12 virtual antennas and SDK ≥ 3.1, the demo uses 1022 range bins. A parser assuming 1024 will mis-slice every range profile and heat map. Does not affect you at 256 ADC samples, but will if you raise it. |
| **`numDopplerBins` floors** | `input_validations.js:274-293` | Jira MMWSDK-1565: floored to 8 (≤4 chirps, several platforms) or 16 (≤8 chirps, xWR68xx/xWR16xx DSP DPU) rather than the next power of two. |
| **Range-profile Q-format** | `input_validations.js:352-365`, `mmWave.js:2871` | The uint16 range profile is meaningless without: `dB = raw × log2linScale × 20log₁₀(2) + dspFftScaleCompAll_log`, where `log2linScale = (1/256) × 2^⌈log₂(N_virt)⌉ / N_virt`. For your config: `0.0052083 × 6.0206 dB − 18.0618 dB`. None of this is in the stream spec. |
| **Heat-map I/Q** | `mmWave.js:2961-2965` | int16 real/imag interleaved, sign-corrected at 32767, antenna index fastest, then `NUM_ANGLE_BINS` FFT, `fftshift`, `fliplr`. |
| **Range-bias correction** | `mmWave.js:2875,3310` | `compRxChanCfg.rangeBias` is subtracted from the range axis of the range profile and heat maps, then clamped to ≥ 0 — but is **not** applied to the point cloud. Point-cloud range and range-profile range are on slightly different axes. |
| **Frame header version field** | `mmWave.js:2226` | uint32 = `major<<24 | minor<<16 | bugfix<<8 | build`; the app's `sdkVersionUint16` is `(major<<8) | minor`. |
| **`configDataPort` handshake** | `mmWave.js:4235-4248,4295` | For SDK ≥ 3.4 the Visualizer sends `version`, then `queryDemoStatus`, then `configDataPort <baud> 1` before the .cfg. The trailing `1` requests an ack on the data port. Skipping this can leave the data port silent. |
| **Comment lines are sent to the device** | `mmWave.js:4217-4219` | The `%`-filter is commented out; `%` lines go over the wire and the device answers `Skipped`. |
| **`adcStartTime` unit bug (TI)** | `input_validations.js:833` vs `:334` | The app reads the cfg's `adcStartTime` token (µs) into `adcStartTimeConst` and then scales it by 10 ns as if it were in device units — a 100× error in one term of `centerFreq_actual`. The term is sub-ppm of the carrier, so it changes nothing numerically. `cfgparse.py` reproduces TI's arithmetic deliberately, for bit-compatibility, and flags it here. |

---

## 4. Findings: network and offline independence

| Component | Behaviour |
|---|---|
| `ApplicationServer.js:277` | HTTP server binds `127.0.0.1` only, on port `0` — an OS-assigned ephemeral port, different every launch. Never externally reachable. |
| `ApplicationServer.js:157-164` | Serves `server-config.js` with `{isOnline: false}`. Offline mode is already the default. |
| `ApplicationServer.js:256-275` | Spawns `runtime\TICloudAgentHostApp\ticloudagent.bat not_chrome`, a **second** localhost server on its own dynamic port. This is the component that actually opens the serial ports. `offline` is forced true. |
| `launcher.json` | Runs `ApplicationServer.js` under `runtime\TICloudAgentHostApp\node.exe`, with `--browser=../runtime/node-webkit/nw.exe`. |
| `package.json` | `"chromium-args": "--ignore-certificate-errors --disable-web-security --user-data-dir"` and `"node-remote": "*://*/*"` — the embedded browser runs with web security disabled and grants Node access to any remote origin. Nothing exploits this offline, but it is a good independent reason not to keep it in the loop. |
| `mmWave.js:388-419` | The only outbound URLs (`dev.ti.com`, `ti.com/lit/pdf/swru529`) are behind Help menu items and fire only on click. |

**Conclusion:** the Visualizer is not phoning home, but it does stand up two
localhost HTTP servers on random ports and a Chromium instance with web security
off, purely to reach a COM port. `link.py` replaces all of it with `pyserial`.
Nothing in this package opens a socket.

---

## 4b. Deep dive: the full JS receive path, byte by byte

This is what happens between a byte arriving on the data COM port and a pixel
changing on screen. Everything below is `mmWave.js:298-362` unless noted.

```
                       serial data port (921600 baud)
                                  |
                        TI Cloud Agent native host
                                  |
                    localhost WebSocket -> gc databind
                                  |
        DATA_port.$rawData  addStreamingListener.onDataReceived(data)
                                  |
        +-------------------------+--------------------------+
        |                                                    |
   (A) saveStreamData(data)                       (B) display pipeline
        |  mmWave.js:305 -- FIRST statement            mmWave.js:307-361
        |  raw bytes, no parsing, no framing
        v
   streamWriter.write(data)  ->  .dat on disk
```

### Branch A — how the `.dat` is created

1. **Start.** `onRecordPause` (`mmWave.js:784-797`) is the Record button.
   `saveStreamStart(getUniqueFileName('processed_stream', '.dat'))` builds the
   name `<platform>_processed_stream_<ISO8601 with _ separators>.dat` — which is
   exactly the shape of your existing filenames — then
   `streamSaver.createWriteStream(filename)` opens a streaming writer
   (`mmWave.js:125-128`).
2. **Every chunk.** `saveStreamData(data)` (`mmWave.js:144-158`) does
   `streamWriter.write(data)` and adds to `savedStreamBytes`. **No parse, no
   magic-word check, no framing, no filtering.** The chunk is whatever the OS
   handed up — it is not frame-aligned, and the first chunk usually starts
   mid-frame, which is why a TI `.dat` often does not begin with a magic word.
3. **Auto-stop.** `mmWave.js:151-156`: recording stops itself when
   `savedStreamBytes >= file_size_limit_MB * 1024 * 1024`, **or** when elapsed
   wall time exceeds `record_time` seconds. Both come from textboxes in the UI.
   The toast reads "Recording has been stopped as file/time max limit has
   reached". If a long capture ended early, this is why.
4. **Stop.** `saveStreamStop` (`mmWave.js:130-137`) closes the writer and then
   calls `onExportTunedProfile()`, which writes the `.cfg` alongside — that is
   how the paired `.cfg` files appear.

**Consequence:** the `.dat` is a byte-exact copy of the data-port octet stream
for the interval recording was active. There is no place in this branch where a
point could be filtered, because at this point nothing has been decoded.

### Branch B — the display pipeline, which *is* lossy

```
dataframe += data                                        mmWave.js:307-313
  |   (if no dataframe yet, only start one when data begins with magic)
  v
while dataframe has bytes:                                mmWave.js:315-331
  |  if dataframe starts with magic:
  |      total_payload_size_bytes = totalFrameSize(...)
  |  else:
  |      dataframe = []            <-- DISCARDS THE WHOLE BUFFER  :319-322
  |  if enough bytes: extractDataFrame() -> push onto dataFrameQueue
  v
extractDataFrame:                                         mmWave.js:269-277
  |  pushes onto dataFrameQueue ONLY if
  |      initComplete && !in_process1 && onPlotsTab && tprocess1     :271
  |  otherwise the frame is silently dropped from the display
  v
if dataFrameQueue.length > 0 && initComplete:             mmWave.js:333-357
  |  again gated on !in_process1 && onPlotsTab
  |  drains numDataFrameAdded frames through tprocess1 -> process1()
  v
process1(bytevec)                                         mmWave.js:2212
```

Three distinct loss mechanisms, none of which touch the file:

| Line | Mechanism |
|---|---|
| `mmWave.js:271` | Frames are only queued when `onPlotsTab === true` and `in_process1 === false`. Sitting on the Configure tab, or a busy render, means frames never enter the display pipeline. |
| `mmWave.js:319-322` | If the accumulated buffer does not start with a magic word, `dataframe = []` throws away everything buffered — no resync attempt, no accounting. |
| `mmWave.js:2384` | `droppedFrames += curPlotServiced - (lastPlotServiced + 1)` — TI explicitly tracks the gap, confirming it is expected. |

### `process1` — the parse itself applies no thresholds

`mmWave.js:2212-2393`:

1. Validate magic and length, else return (`:2214-2237`).
2. Read the 40-byte header (`:2222-2263`).
3. Loop `numTLVs`, dispatch on **type** (`:2293-2328`). Heavy TLVs are processed
   inline; types 1, 2 and 7 are only *recorded as offsets* and deferred.
4. Process the deferred ones in a forced order (`:2330-2357`):
   **side info → detected points → range profile**. Required because `peakVal`
   is derived from side info (`:2672`) and the range-profile plot needs the
   detected-point range indices (`:2908-2922`).
5. `getXYZ_type2` (`:2558-2605`) reads 16 bytes per point as four float32 LE and
   copies **every** point. There is no threshold, no FoV test, no SNR gate, no
   deduplication anywhere in the decode path.

The only conditional that can skip point decoding entirely is
`if (Params.detectedObjectsToPlot == 1)` at `:2640` — a *display* switch derived
from `guiMonitor`, not a data filter. When it is 0 the points are never drawn,
but the bytes are already on disk.

### Where the display genuinely alters what you see

| Effect | Source | What it does |
|---|---|---|
| **Point accumulation** | `:2689-2706`, `:4383-4406` | plot1 shows `round(slider_seconds × 1000 / framePeriodicity)` frames of superimposed points. A ring of per-frame counts drives how many are spliced off the front each frame. |
| **Asymmetry between plots** | `:2439-2441` vs `:2487-2488` | plot1 (X-Y / 3D scatter) uses the **accumulated** buffer; plot3 (Doppler-Range scatter) uses **only the current frame**. The same frame therefore shows a different number of points in the two panels. |
| **Colormap forced off** | `:4397-4401` | When `aggFrames > 1` the colour dropdown is hidden and forced to "off", because intensity is not accumulated. |
| **Range-bias shift** | `:2875`, `:3310`, `:2985` | `compRxChanCfg.rangeBias` is subtracted from the range axis of the range profile and both heat maps, then clamped at 0 — but **not** from the point cloud. |
| **Reconstructed intensity** | `:2672` | The "intensity" colour axis is `snrDB + noiseDB`; no peak value is transmitted. |

`viz.py` reproduces all of this, with accumulation **off by default** so that
what you see is one frame — use `--accumulate <seconds>` to get TI's behaviour
back for comparison.

---

## 5. What to run

Four commands cover everything: pick a config, record, look, audit.

### Step 0 — pick a capture config

There are **four presets**, and choosing the wrong one cannot be fixed later. If
a payload was not enabled at capture time, the bytes were never transmitted.

```bash
python -m mmwave_suite.extraction.highfidelity.configs          # compare all four
python -m mmwave_suite.extraction.design --preset gait          # full report for one
```

| preset | fps | rng x dop | extra payload | use for |
|---|---|---|---|---|
| `ghost` | 10 | 128 x 128 | TLV 8 (angle I/Q) | **the go-to** — multipath / ghost work, empty-room reference |
| `gait` | **25** | 128 x 64 | none | walking, micro-Doppler, limb motion |
| `doppler` | 8 | 64 x 64 | TLV 5 (RD map) | dense pre-CFAR range-Doppler, Tier B RT/VT |
| `survey` | 5 | 64 x 64 | TLV 5 + TLV 8 | careful static-scene study |

**`ghost` is the default choice** — it is the only preset that can answer the
ghost-vs-human question, because angle is the only discriminator a specular image
cannot hide from. Capture `gait` as a second pass when gait metrics matter.

**Frame rate is the parameter that bites.** Doppler resolution comes from the
chirps inside one frame; the frame rate sets how finely you can watch the gait
cycle evolve. A stride is roughly 1 s, so 5 fps gives about 5 samples per stride
and cannot resolve limb swing regardless of Doppler bin count. See
[cfg/README.md](cfg/README.md) for the full reasoning, a parameter-by-parameter
reference, and [cfg/archive/](cfg/archive/) for what the superseded presets
offered.

Every preset is validated against both the radar-cube L3 limit and the UART
budget before it is written — enabling a heat map without shrinking the geometry
pushes the device past ~92 KB/s and it tears frames, which looks like a hardware
fault rather than a config mistake.

### Step 1 — record a `.dat` (replaces Record Start / Record Stop)

```bash
python -m mmwave_suite.extraction.live --list-ports      # find your two COM ports

python -m mmwave_suite.extraction.live --cli COM4 --data COM5 \
       --cfg mmwave_suite/extraction/cfg/xwr68xx_AOP_gait_25fps_points.cfg \
       --out runs --label "behind drywall 3m" --countdown 5 --seconds 60
```

Writes `runs/<name>/<name>.dat` — same format as TI's, so it still replays in
the Visualizer — plus the `.cfg` actually sent, a `.meta.json`, and a per-frame
`.idx.jsonl` with host timestamps. No plots, minimal CPU; use this whenever the
data matters.

Two flags worth knowing:

* `--label "..."` is appended to the timestamped run name and stored verbatim in
  `meta.json`. `--label "behind drywall 3m"` gives
  `run_20260929_142233_behind-drywall-3m`. Six weeks later this is the only
  thing that tells you what a recording was.
* `--countdown N` counts down N seconds and prints `>>> START WALKING <<<` at
  the instant the sensor actually starts. The countdown is inserted *between*
  the parameter commands and `sensorStart`, not before the whole config, because
  a config takes ~44 command round-trips to send — counting down first would put
  "GO" a second or two ahead of frame 1, which is exactly the drift it removes.

### Step 2 — see the plots (replaces the Visualizer Plots tab)

```bash
# live from the EVM, and record at the same time
python -m mmwave_suite.extraction.viz --cli COM4 --data COM5 --cfg profile.cfg --out ./runs

# replay a recording at its real frame rate
python -m mmwave_suite.extraction.viz --dat capture.dat --cfg capture.cfg

# replay fast, TI-style point persistence for comparison
python -m mmwave_suite.extraction.viz --dat capture.dat --cfg capture.cfg --fast --accumulate 3
```

Useful flags: `--accumulate SECONDS` (0 = one frame, the default),
`--range-width` / `--range-depth` (TI's Plot Settings boxes, default 5 / 10 m),
`--linear` (range profile on a linear axis instead of dB), `--fast`.

### Step 3 — check a recording's integrity

```bash
python -m mmwave_suite.extraction.audit capture.dat --cfg capture.cfg
```

### Capture integrity: use `live.py`, not `viz.py`, for data that matters

Measured on a real 10-minute run (5 898 frames, xWR68xx_AOP at 10 fps, plots on):

```
lost_bytes=8386 (0.18%)  resyncs=11  corrupted frames=11 (0.19%)
```

Eleven discrete loss events, roughly one per 54 s, each with an identical
signature: one frame truncated after TLV 2 (losing TLVs 6 and 9), and the next
frame gone entirely, then a clean resume. That is one contiguous run of bytes
dropped by the **host**, not the device — a receive-buffer overflow while the
reader was starved, almost certainly by matplotlib holding the GIL during a 3D
scatter redraw.

Mitigations now in place: the receive buffer is enlarged to 1 MB and a warning
is printed if the driver refuses; `viz.py` drains up to 64 frames per tick; and
`resyncs` is now counted correctly on the live path (it previously reported 0
while bytes were being lost — wrong in the reassuring direction).

Even so, **the plotting path is the risk**. For captures that feed analysis, run
`live.py` (no matplotlib) and use `viz.py` only for setup and monitoring. Always
check `resyncs` in the run summary, or run `audit.py` afterwards: a healthy
capture reports `resyncs=0`.

### Always let the handshake stop a running sensor

If the EVM is still streaming when you attach — the normal state after closing
the TI Visualizer, reported as `sensor_state=2` — frames produced by the
**previous** configuration would otherwise be recorded before your `.cfg` takes
effect. In one run this put 35 foreign frames at the head of the file, visible
as a frame counter that jumps `67298 -> 1` mid-recording. `handshake()` now
issues `sensorStop` before the reader starts. If you have older recordings, look
for a counter reset near the beginning and discard the frames before it.

---

## 5b. Reference details

### Audit an existing recording

```bash
python -m mmwave_suite.extraction.audit path/to/file.dat --cfg path/to/matching.cfg
```

Reports byte accounting, TLV composition and ordering, header-vs-payload
consistency, what TI's reference parser would lose on that file, SNR sign
statistics, and the on-chip gates from the .cfg. `--json out.json` for machine
consumption.

Always pass the .cfg that was used for **that** capture — the audit
cross-checks derived range-bin count against what is actually in the stream.

### What a run directory contains

Every `live.py` capture produces, per run:

* `<name>.dat` — raw UART bytes, same format as a TI recording, so it still
  plays back in the Visualizer and in your existing `dat_parser_plots` pipeline
* `<name>.cfg` — the configuration actually sent
* `<name>.meta.json` — ports, device version, derived geometry, on-chip filters
* `<name>.idx.jsonl` — one line per frame: host timestamp, frame number, CPU
  cycles, point count, TLV order, anomalies. This is what the Visualizer never
  gives you and what a digital twin needs.

### As a library

```python
from mmwave_suite.extraction import parse_cfg, parse_dat, StreamParser

cfg = parse_cfg("profile.cfg")
print(cfg.summary())

frames, stats = parse_dat("capture.dat", geometry=cfg.geometry())
for f in frames:
    xyz   = f.points                 # structured array: x, y, z, doppler
    snr   = f.snr_db                 # signed, dB
    rng   = f.range_m
    az    = f.azimuth_deg            # atan2-based, unlike TI's atan
    rp_db = cfg.range_profile_db(f.range_profile)
```

Real-time:

```python
parser = StreamParser(geometry=cfg.geometry())
for frame in parser.feed(chunk_from_serial, host_timestamp=t):
    ...
```

---

## 6. Architecture

Three decoupled stages, so a slow consumer can never cost frames — the inverse
of the Visualizer's failure mode:

```
[reader thread]   pure I/O: read(), timestamp, hand off. Never parses.
      | queue
[parser thread]   writes raw bytes to .dat FIRST (same ordering as
      | queue     mmWave.js:305), then StreamParser.feed() -> Frames
[your callback]   runs on the parser thread, or drain frame_queue yourself
```

Because recording happens before parsing, the `.dat` is byte-complete by
construction. Parsing back-pressure shows up as measurable queue depth, not as
missing data.

`azimuth_deg` uses `atan2(x, y)` rather than TI's `atan(x/y)`
(`parser_mmw_demo.py:246`). `atan` folds the `y < 0` half-plane onto the `y > 0`
half-plane, so a target behind the array reports at the mirrored forward angle —
a distinction that matters when the thing you are classifying is a mirror image.

---

## 7. Status and next steps for the ghost-filtering work

Done, and no longer open questions:

* **The `.dat` is a verbatim copy of the UART stream.** Established in §1 against
  both the Visualizer source and 14 recordings. The loss is on-chip (§1.4), not
  host-side.
* **Empty frames are kept.** `tlv.py` dispatches on TLV *type* and treats a
  zero-detection frame as valid data, so the 34–93% losses in §2.1 no longer
  happen. An absent detection is evidence; the gaps are what a tracker needs in
  order not to hallucinate continuity.
* **Every capture records its own `.cfg`.** `live.py` copies it into the run
  directory, so no recording has guessed axes.
* **TLV 8 works and its payload size is now measured, not inferred.** On
  `run_20260805_163244_tlv8-probe`: 76/76 frames, 0.0000% bytes lost, zero parse
  anomalies, payload 3072 bytes = `4 x 12 virtual ant x 64 range bins`, decoding
  to a `(64, 12) complex64` array. The earlier worry that the real payload might
  be twice the inferred size — which would have put a full-resolution angle
  config at 139% of the UART — is settled: the inference was right.

Still open, in priority order:

1. **Recapture the 2026-09-26 session with a production preset.** Every run that
   day used `angle-probe`, a 5 fps / 32-Doppler-bin diagnostic that was meant to
   be run once and replaced. At 5 fps you get roughly 5 samples per stride, so
   the gait metrics from that session are not trustworthy — this is a Nyquist
   problem in the capture, and no analysis code can repair it. Use `gait` for
   walking subjects, `ghost` when angle is needed. The preset has been deleted so
   it cannot be picked again.

2. **Treat `multiObjBeamForming` as an experimental variable, not a default.** It
   reports secondary angular peaks at ≥50% of the dominant peak within the same
   range-Doppler bin — a documented generator of exactly the ghost points being
   classified. Capture matched pairs at `-1 1 0.5` and `-1 0 0.5`: points present
   in the first and absent in the second are device-declared secondary peaks by
   construction. This is the cheapest ablation available and it has not been run.

3. **Capture an empty-room reference with the `ghost` preset.** Needed for
   background subtraction and as the control arm of the panel intervention in
   `GHOST_VALIDATION_PLAN.txt`. Same config, `--label empty-room`.

4. **Decide whether the thesis needs Tier B.** The `doppler` preset gives the
   dense pre-CFAR range-Doppler spectrum, which is the only way to get the
   paper's Eq. (1) VT map verbatim instead of a sparse reconstruction from CFAR
   points. It costs angle information and drops to 8 fps, so it is a separate
   capture rather than an upgrade — see [analysis fidelity tiers](../analysis/README.md).
