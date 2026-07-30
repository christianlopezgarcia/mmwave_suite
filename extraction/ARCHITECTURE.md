# How the TI Visualizer works, and how `mmwave_direct` mirrors it

A stage-by-stage walkthrough of the path from radar chip to pixel, with TI's
implementation on the left and ours on the right. Every TI claim cites a line in
`C:\ti\guicomposer\runtime\gcruntime.v11\mmWave_Demo_Visualizer\app\`.

---

## 0. The pipeline at a glance

```
  [1] radar DSP        CFAR -> peak grouping -> AoA -> FoV gating -> point cloud
        |                        *** THIS is where points are dropped ***
        |  UART, 921600 baud, TLV-framed
  [2] host transport   TI: Cloud Agent -> localhost WS -> gc databind
                       US: pyserial read() on a thread
        |
  [3] raw bytes        TI: saveStreamData() -> .dat        (verbatim, no parse)
                       US: _on_chunk()      -> .dat        (verbatim, no parse)
        |
  [4] reassembly       magic word -> totalPacketLen -> whole frames
        |
  [5] TLV dispatch     header(40B) + N x (type,len,payload) + 32B-alignment pad
        |
  [6] scaling          Q-format / FFT-compensation -> physical units
        |
  [7] rendering        5 plots
```

Stages 1-5 are where scientific truth lives. Stages 6-7 are presentation.

---

## 1. On the radar DSP, before any byte is sent

Nothing on the host removes detections. The chip does, driven by your `.cfg`:

| Command | Effect |
|---|---|
| `cfarCfg <sf> 0 ... <thr> <grp>` | Range-direction CFAR threshold (dB) and peak grouping |
| `cfarCfg <sf> 1 ... <thr> <grp>` | Doppler-direction CFAR threshold and peak grouping |
| `cfarFovCfg <sf> 0 <min> <max>` | Range gate, in **range-index** space (so bin rounding lets points sit marginally outside the stated metres) |
| `cfarFovCfg <sf> 1 <min> <max>` | Doppler gate |
| `aoaFovCfg <sf> <azMin> <azMax> <elMin> <elMax>` | Angle gate |
| `multiObjBeamForming <sf> <en> <thr>` | Reports **secondary** angular peaks in the same range-Doppler bin at >= `thr` of the dominant peak |
| `clutterRemoval <sf> <en>` | Static (zero-Doppler) return removal |
| `extendedMaxVelocity <sf> <en>` | Velocity disambiguation beyond the nominal unambiguous range |

For multipath/ghost work `multiObjBeamForming` is the significant one: it is a
mechanism that deliberately emits extra points at the same range and Doppler but
a different angle — structurally identical to a specular ghost.

---

## 2. Getting bytes off the wire

**TI.** `launcher.exe` starts `ApplicationServer.js`, an HTTP server bound to
`127.0.0.1` on an OS-assigned ephemeral port (`ApplicationServer.js:277`). It
serves `server-config.js` with `{isOnline:false}` (`:157-164`) and spawns
`TICloudAgentHostApp\ticloudagent.bat` (`:256-275`), a **second** localhost
server that owns the actual COM ports. A node-webkit Chromium instance —
launched with `--disable-web-security` and `node-remote: "*://*/*"`
(`package.json`) — connects back over WebSocket, and the bytes surface in the
page as `DATA_port.$rawData`.

**Ours.** `link.py` calls `serial.Serial(port, 921600).read()` on a dedicated
thread. Two localhost servers and a browser removed. No socket is opened.

---

## 3. Writing the `.dat` — the critical question

**TI**, `mmWave.js:298-362`:

```js
onDataReceived: function (data) {
    if (data) {
        if (Params) {
            var numDataFrameAdded = 0;
            saveStreamData(data);      // <-- mmWave.js:305, FIRST statement
            ...
```

`saveStreamData` (`mmWave.js:144-158`) is `streamWriter.write(data)` and nothing
else. It runs **before** the magic-word check, before framing, before parsing.
Consequences:

* The `.dat` is a verbatim octet copy of the data port for the recorded interval.
* Chunks are *not* frame-aligned, so a TI `.dat` usually starts mid-frame.
* Recording auto-stops on a size **or** wall-clock limit (`mmWave.js:151-156`).
* `saveStreamStop` also writes the `.cfg` alongside (`mmWave.js:130-137`).

**Ours**, `live.py::_on_chunk`: identical ordering — write the raw chunk, then
queue it for parsing. Deliberate divergences: we do not implement the auto-stop
(use `--seconds`), and we additionally emit `.idx.jsonl` with a host timestamp
and byte offset per frame, which TI has no equivalent of.

---

## 4. Reassembly — where TI's display quietly loses frames

**TI**, `mmWave.js:307-331`:

```js
if (dataframe) { Array.prototype.push.apply(dataframe, data); }
else if (data.length >= 16 && isMagic(data, 0)) { dataframe = data.slice(0); }

while (dataframe && dataframe.length > 0) {
    if (dataframe.length >= 16 && isMagic(dataframe, 0)) {
        Params.total_payload_size_bytes = totalFrameSize(dataframe, 8 + 4);
    } else {
        dataframe = [];                       // <-- :319-322 DISCARD EVERYTHING
        Params.total_payload_size_bytes = 0;
    }
    if (dataframe.length >= Params.total_payload_size_bytes) {
        dataframe = extractDataFrame(dataframe);
    } else break;
}
```

and `extractDataFrame` (`mmWave.js:269-277`) only enqueues when

```js
if (initComplete === true && in_process1 === false && onPlotsTab === true && tprocess1)
```

So the display drops frames when you are not on the Plots tab, or when a redraw
is still running; and on any desync it throws the whole buffer away with no
resync attempt and no accounting. TI tracks the damage at `mmWave.js:2384`
(`droppedFrames`) and warns about it at `:2389-2391`.

**Ours**, `tlv.py::StreamParser.feed`: same magic + `totalPacketLen` chaining,
but on desync we scan forward to the next magic word, count the skipped bytes in
`bytes_skipped`, and increment `resyncs`. There is no display gate — parsing is
on its own thread and cannot influence what was recorded. We also validate the
header before accepting a magic word (`_header_is_plausible`), because the
32-byte padding is uninitialised and can contain a magic sequence by chance;
TI has no such guard.

---

## 5. Frame and TLV structure

### Frame header — 40 bytes (`mmWave.js:2222-2263`)

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | magic `02 01 04 03 06 05 08 07` |
| 8 | 4 | `version` = major<<24 \| minor<<16 \| bugfix<<8 \| build |
| 12 | 4 | `totalPacketLen` — **includes** header and 32-byte padding |
| 16 | 4 | `platform` (e.g. `0xA6843`) |
| 20 | 4 | `frameNumber` |
| 24 | 4 | `timeCpuCycles` |
| 28 | 4 | `numDetectedObj` |
| 32 | 4 | `numTLVs` |
| 36 | 4 | `subFrameNumber` |

TI derives `sdkVersionUint16` as `(major<<8)|minor` from bytes 2 and 3 of the
version word (`mmWave.js:2226`).

### TLVs

Each is `uint32 type`, `uint32 length`, then payload. **`length` excludes the
8-byte TLV header** — confirmed by `mmWave.js:2296` advancing 8 then `:2327`
advancing `tlvlength`.

| Type | Name | Payload |
|---|---|---|
| 1 | DETECTED_POINTS | `numDetectedObj` x 16 B: float32 LE x, y, z, doppler |
| 2 | RANGE_PROFILE | `numRangeBins` x uint16 log-magnitude |
| 3 | NOISE_PROFILE | `numRangeBins` x uint16 |
| 4 | AZIMUT_STATIC_HEAT_MAP | `numTxAzimAnt*numRxAnt*numRangeBins` x (int16 I, int16 Q) |
| 5 | RANGE_DOPPLER_HEAT_MAP | `numDopplerBins*numRangeBins` x uint16 |
| 6 | STATS | 6 x uint32 |
| 7 | DETECTED_POINTS_SIDE_INFO | `numDetectedObj` x (int16 snr, int16 noise), 0.1 dB steps |
| 8 | AZIMUT_ELEVATION_STATIC_HEAT_MAP | `numVirtualAnt*numRangeBins` x (int16 I, int16 Q) — AOP |
| 9 | TEMPERATURE_STATS | int32 valid, uint32 time, 10 x int16 |

### The 32-byte padding (undocumented)

`totalPacketLen` is always rounded up to a multiple of 32
(`MMWDEMO_OUTPUT_MSG_SEGMENT_LEN`). Verified empirically: every frame observed is
`0 mod 32`, with 0-28 bytes after the last TLV. **The pad is not zeroed** — one
sample was `20 54 00 08`. Two consequences:

1. Summing TLV lengths never equals `totalPacketLen`.
2. Padding can contain a magic-word sequence, so frame boundaries must be chained
   through `totalPacketLen` and never rediscovered by scanning.

### Heat-map matrices are column-major on the wire (undocumented)

`mmWave.js:3306` calls `MyUtil.reshape(rangeDoppler, numDopplerBins, numRangeBins)`,
and `MyUtil.reshape` (`myutil.js:87-102`) is a **MATLAB column-based** reshape —
`i = c*rows + r`. So in the TLV-5 payload the **Doppler index varies fastest**
and range is the outer index, matching the SDK's `detMatrix[range][doppler]`.

A naive `numpy.reshape(numDopplerBins, numRangeBins)` produces an array of the
*same shape* but transposed contents. Verified on a synthetic 16 x 256 payload:
**4080 of 4096 cells differ**. Nothing downstream can detect it, because the
shape is right — the heat map simply renders as noise. Correct forms:

```python
raw.reshape(num_range_bins, num_doppler_bins).T        # what we now do
raw.reshape(num_doppler_bins, num_range_bins, order='F')   # equivalent
```

### Dispatch order

TI loops `numTLVs` and switches on **type** (`mmWave.js:2293-2328`), recording
byte offsets for types 1, 2 and 7 rather than processing them inline, then runs
them in a forced order (`:2330-2357`):

> **side info → detected points → range profile**

because `peakVal` is derived from side info (`:2672`) and the range-profile
overlay needs the detected-point range indices (`:2908-2922`).

`tlv.py` dispatches on type identically, and is order-agnostic — it returns a
`Frame` with named fields, so consumers do not depend on wire order at all. (TI's
own reference `parser_mmw_demo.py` assumes TLV#1 is type 1 and TLV#2 is type 7,
which is what makes it fragile.)

---

## 6. Scaling — turning integers into physics

This is the part with no public specification. Three groups of constants.

### 6a. Geometry (`input_validations.js:261-350`)

```
numChirpsPerFrame  = (chirpEndIdx - chirpStartIdx + 1) * numLoops
numDopplerChirps   = numChirpsPerFrame / numTxAnt
numDopplerBins     = 1 << ceil(log2(numDopplerChirps))     [with platform floors]
numRangeBins       = 1 << ceil(log2(numAdcSamples))        [1024 -> 1022 on 12 virt ant]
```

Then the **BSS frequency quantisation** (`:310-334`), which is why you cannot
just use the numbers from the `.cfg`:

```
CLI_FREQ_SCALE_FACTOR = 2.7 (60 GHz) or 3.6 (77 GHz)
freqSlopeConst_actual = trunc(freqSlopeConst * 2^26 / scale) * scale / 2^26
startFreq_actual      = trunc(startFreq      * 2^26 / scale) * scale / 2^26
centerFreq_actual     = startFreq_actual
                      + 0.5*(freqSlopeConst_actual*numAdcSamples/digOutSampleRate)
                      + freqSlopeConst_actual*(adcStartTime*10e-9)
```

and finally

```
rangeIdxToMeters     = 3e8 * digOutSampleRate*1e3 / (2*|freqSlope_actual|*1e12*numRangeBins)
dopplerResolutionMps = 3e8 / (2*centerFreq_actual*1e9*(idleTime+rampEndTime)*1e-6
                              * numDopplerBins * numTxAnt)
```

`cfgparse.py::_derive` is a direct port, including `Math.trunc` semantics.

### 6b. FFT scaling — **the answer to "where was the FFT scaling math?"**

It lives in **three separate files**, which is why it is easy to miss:

**(i) Definition — `mmWave.js:1808-1820`**

```js
var dspFftScalComp2 = function (fftMinSize, fftSize) {
    sLin = fftMinSize / fftSize;
    return sLin;
}

var dspFftScalComp1 = function (fftMinSize, fftSize) {
    smin = (Math.pow((Math.ceil(Math.log2(fftMinSize) / Math.log2(4) - 1)), 2)) / (fftMinSize);
    sLin = (Math.pow((Math.ceil(Math.log2(fftSize)    / Math.log2(4) - 1)), 2)) / (fftSize);
    sLin = sLin / smin;
    return sLin;
}
```

`comp2` is the HWA (hardware accelerator) FFT compensation — a plain ratio.
`comp1` is the DSP FFT compensation, with a radix-4 stage count.

**(ii) Selection — `input_validations.js:380-437`**, a five-way platform switch,
because the compensation depends on whether the Range and Doppler DPUs are the
DSP or the HWA implementation on that silicon:

| Platform | 1D (range) | 2D (doppler) |
|---|---|---|
| xWR16xx | `comp1(64, numRangeBins)` | `comp2(16, numDopplerBins)` |
| xWR18xx, xWR18xx_AOP | `comp2(32, numRangeBins)` | `1` |
| xWR64xx | `comp2(32, numRangeBins)` | `1` |
| xWR68xx | `comp2(32, numRangeBins)` | `1` if SDK < 3.2, else `comp2(16, numDopplerBins)` |
| **xWR68xx_AOP** | `comp2(32, numRangeBins)` | `1` |

then `dspFftScaleCompAll_log = 20log10(comp1D) + 20log10(comp2D)` (`:436-437`).

**(iii) Application — `mmWave.js:2865-2873`**, inside `processRangeNoiseProfile`:

```js
if (Params.rangeProfileLogScale == false) {
    ary[idx] = Params.dspFftScaleCompAll_lin[sf] * Math.pow(2, value * Params.log2linScale[sf]);
} else {
    ary[idx] = value * Params.log2linScale[sf] * Params.toDB + Params.dspFftScaleCompAll_log[sf];
}
```

with the Q-format constants from `input_validations.js:352-365`:

```
NumVirtAnt   = numTxAnt * numRxAnt
log2linScale = (1/256) * 2^ceil(log2(NumVirtAnt)) / NumVirtAnt
               [(1/512) on xWR18xx/xWR68xx below SDK 3.2]
toDB         = 20 * log10(2)
```

**The complete chain.** The device transmits `log2(magnitude)` in a fixed-point
format. So:

```
dB = raw_uint16 * log2linScale * 20log10(2) + dspFftScaleCompAll_log
```

For your xWR68xx_AOP config (12 virtual antennas, 256 range bins):

```
log2linScale = (1/256) * 16/12          = 0.0052083333
toDB         = 20*log10(2)              = 6.0206
comp1D       = comp2(32, 256) = 32/256  = 0.125   -> -18.0618 dB
comp2D       = 1                                  ->   0.0000 dB

dB = raw * 0.0052083333 * 6.0206 - 18.0618
   = raw * 0.031357 - 18.0618
```

A raw value of 3700 gives 98 dB, which is what the range-profile panel shows.

**In our code**, the same three stages:

| Stage | TI | `mmwave_direct` |
|---|---|---|
| Definition | `mmWave.js:1808-1820` | [`cfgparse.py:297`](cfgparse.py) `_dsp_fft_scal_comp2`, [`:309`](cfgparse.py) `_dsp_fft_scal_comp1` |
| Selection | `input_validations.js:380-437` | [`cfgparse.py:590-609`](cfgparse.py) platform switch in `_derive()` |
| Q-format | `input_validations.js:352-365` | [`cfgparse.py:583-586`](cfgparse.py) `log2lin_scale`, `to_db` |
| Application | `mmWave.js:2865-2873` | [`cfgparse.py:229`](cfgparse.py) `range_profile_db()`, [`:242`](cfgparse.py) `range_profile_linear()` |
| Consumed by | `plot2` traces | [`viz.py:378`](viz.py), [`:394`](viz.py) |

> **Both of our FFT functions were wrong in the first draft** and were corrected
> after reading `mmWave.js:1808-1820` directly. `comp2` had been written as
> `1/2^log2(fftSize//fftMinSize)`, which is identical for exact powers of two —
> so the AOP result `-18.0618 dB` never moved and the error was invisible — but
> diverges by 0.26 dB at `numRangeBins = 1022` and raises `ValueError` for
> `comp2(16, 8)`, which is reachable on non-AOP 6843 boards. `comp1` was wrong
> outright. This is the single best argument for reading the reference rather
> than reconstructing from the shape of the output.

### 6c. Per-point scaling

* Point cloud: **float32**, no Q-format. `x, y, z, doppler` in m and m/s
  (`mmWave.js:2586-2589`).
* Side info: **signed** int16, `× 0.1` dB (`mmWave.js:3362, 3374`).
* `peakVal` is **not transmitted** — TI reconstructs it as `snrDB + noiseDB`
  (`mmWave.js:2672`) purely to colour the scatter plot.
* Heat maps: int16 I/Q interleaved, sign-corrected at 32767 (`:2961-2965`).
* Range bias (`compRangeBiasAndRxChanPhase` field 1) is subtracted from the range
  axis of the range profile and both heat maps and clamped at 0
  (`:2875, :3310, :2985`) — but **not** from the point cloud.

---

## 7. Rendering (`mmWave.js:3719-4076`)

| Panel | Shown when | Source | Note |
|---|---|---|---|
| plot1 | `detectedObjects` | accumulated point cloud | 3D scatter on AOP/elevation parts, else 2D X-Y with polar grid |
| plot2 | `logMagRange \|\| noiseProfile` | TLV 2 / 3 | 3 traces: profile, detected points at zero Doppler, noise |
| plot3 | `rangeDopplerHeatMap` else `detectedObjects` | TLV 5 or point cloud | heat map, or Doppler-Range scatter of the **current frame only** |
| plot4 | `rangeAzimuthHeatMap` | TLV 4 / 8 | angle FFT + griddata onto a 100x100 Cartesian grid |
| plot5 | `statsInfo` | TLV 6 | rolling 100-frame CPU load |

Two behaviours worth knowing:

**Accumulation.** `scatterDisplayTime` (`mmWave.js:4383-4406`) computes
`aggFrames = round(sliderSeconds*1000 / framePeriodicity)`, and
`processDetectedPoints` (`:2689-2706`) keeps a ring of per-frame point counts,
splicing the oldest frame's points off the front each frame. **plot1 therefore
shows several seconds of superimposed point clouds, not one frame.**

**Asymmetry.** plot1 reads the accumulated buffer (`:2439-2441`); plot3 reads the
current frame's arrays (`:2487-2488`). The same frame shows a different number of
points in the two panels. Colour mapping is force-disabled when `aggFrames > 1`
(`:4397-4401`) because intensity is not accumulated.

`viz.py` reproduces all of this, with accumulation **off by default** —
`--accumulate <seconds>` restores TI's behaviour.

---

## 8. Function map

| TI | `mmwave_direct` |
|---|---|
| `isMagic` `mmWave.js:2095` | `tlv.MAGIC`, `find_magic` |
| `totalFrameSize` `:2107` | header field `total_packet_len` |
| `searchMagic` `:2112` | `bytes.find(MAGIC)` + `_header_is_plausible` |
| `onDataReceived` `:298` | `link.RadarLink.start_reader` + `live._on_chunk` |
| `saveStreamData` `:144` | `live.LiveCapture._on_chunk` |
| `extractDataFrame` `:269` | `tlv.StreamParser.feed` |
| `process1` `:2212` | `tlv._parse_tlvs` |
| `getXYZ_type2` `:2558` | `tlv.POINT_DTYPE` + `np.frombuffer` |
| `processSideInfo` `:3329` | `tlv.SIDEINFO_DTYPE`, `Frame.snr_db` / `noise_db` |
| `processRangeNoiseProfile` `:2847` | `cfgparse.range_profile_db` + `viz._init_rp` |
| `processAzimuthHeatMap` `:2933` | `viz.azimuth_symbols` + `_angle_fft` |
| `processAzimuthElevHeatMap` `:3017` | `viz._AOP_SYMBOLS` + `cfg.tx_chirp_index` |
| `processRangeDopplerHeatMap` `:3290` | `Frame.range_doppler_heatmap` + `viz` |
| `processStatistics` `:3415` | `Frame.stats` |
| `parseCfg` `input_validations.js:30+` | `cfgparse.parse_cfg` |
| `dspFftScalComp1/2` `mmWave.js:1808` | `cfgparse._dsp_fft_scal_comp1/2` |
| `setupPlots` `:3719` | `viz.Visualizer.__init__` |
| `plotScatterpoints` `:2420` | `viz.Visualizer.update` |
| `cmd_sender_listener` `:4210` | `link.RadarLink` |
| `scatterDisplayTime` `:4383` | `viz.Visualizer.agg_frames` |
| *(none)* | `audit.py` — byte-level integrity checking |

---

## 9. Deliberate divergences from TI

Everything here is intentional. Anything not on this list is meant to match TI
exactly; if it does not, it is a bug.

| # | Divergence | Why |
|---|---|---|
| 1 | No display gate on parsing | TI drops frames when off the Plots tab or mid-redraw (`mmWave.js:271`). We parse everything. |
| 2 | Resync instead of buffer-nuke | TI discards the whole buffer on desync (`:319-322`). We scan to the next magic and count what was lost. |
| 3 | Header plausibility check | The 32-byte pad is uninitialised and can contain a magic word. TI has no guard. |
| 4 | `atan2` for azimuth | TI's reference Python uses `atan(x/y)`, which folds the `y<0` half-plane onto `y>0` — a target behind the array reports at the mirrored forward angle. |
| 5 | Zero-detection frames are valid | TI's reference Python returns `TC_FAIL` for `numDetObj <= 0`. An empty frame is data. |
| 6 | Signed side info | TI's reference Python reads `int16` as unsigned. The JS sign-corrects; we follow the JS. |
| 7 | `sensorStop` when already streaming | TI owns the port continuously and never meets this. We would otherwise record frames from the previous config. |
| 8 | `version` failure is fatal | TI's config path lives inside `askVersion`'s callback and simply never fires. Ours would run on regardless. |
| 9 | `configDataPort` rejection detected | The firmware answers `Done` even when it prints `Ignored:` and keeps the old baud. |
| 10 | No record size/time auto-stop | TI stops on limits from UI textboxes (`:151-156`). Use `--seconds`. |
| 11 | `.idx.jsonl` sidecar | Host timestamps and byte offsets per frame. TI has no equivalent; needed for a digital twin. |
| 12 | Accumulation off by default | TI defaults to a multi-second window, which makes a single frame's ghosts indistinguishable from a trail. |
| 13 | scipy for griddata | TI ships its own `math_gridddata.js` + `delaunay.js`. `scipy.spatial.Delaunay` is equivalent and faster. |
