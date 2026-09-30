# Configurations

Four generated presets, one archival TI reference, and [archive/](archive/) for
the superseded ones. Filenames are self-describing:

```
xwr68xx_AOP_<purpose>_<fps>fps_<extra payload>.cfg
```

Frame rate is in the filename deliberately — picking a 5 fps diagnostic for a day
of walking captures is the exact mistake this is meant to make visible.

| file | fps | rng × dop | extra payload | use for |
|---|---|---|---|---|
| `xwr68xx_AOP_ghost_10fps_tlv8.cfg` | 10 | 128×128 | TLV 8 angle I/Q | **← the go-to.** multipath / ghost work, empty-room reference |
| `xwr68xx_AOP_gait_25fps_points.cfg` | **25** | 128×64 | none | walking, micro-Doppler, limb motion |
| `xwr68xx_AOP_doppler_8fps_tlv5.cfg` | 8 | 64×64 | TLV 5 RD map | dense pre-CFAR range-Doppler, Tier B RT/VT |
| `xwr68xx_AOP_survey_5fps_tlv5+tlv8.cfg` | 5 | 64×64 | TLV 5 + TLV 8 | careful static-scene study |
| `xwr68xx_AOP_ti-baseline_10fps_points.cfg` | 10 | 256×16 | none | **archival only** — the original demo config |

Every generated file carries its own header saying what it is for, what it is
*not* for, its frame rate, its extra payload and its share of the UART:

```
% PRESET         : ghost
% WHAT IT IS     : Multipath/ghost: TLV 8 complex I/Q per virtual antenna
% USE FOR        : multipath / ghost discrimination; also the empty-room reference
% DO NOT USE FOR : gait metrics -- 10 fps is only ~10 samples per stride
% FRAME RATE     : 10.0 fps  (frame period 100 ms)
% EXTRA PAYLOAD  : TLV 8 azimuth-elevation angle I/Q
```

Generate, don't hand-edit — change the preset and regenerate, so the file and the
catalogue can never disagree:

```bash
python -m mmwave_suite.extraction.highfidelity.configs                 # compare all
python -m mmwave_suite.extraction.highfidelity.configs --all --out mmwave_suite/extraction/cfg
```

Definitions and reasoning: [../highfidelity/configs.py](../highfidelity/configs.py).
Every preset is validated against both the radar-cube L3 limit and the UART budget
before it is written.

---

## Which one is the go-to? → `ghost`

**Use `ghost` unless you have a specific reason not to.** The thesis question is
*ghost or human*, and TLV 8 angle I/Q is the only payload that can answer it: a
specular ghost is *defined* by arriving from the wrong direction. Range cannot see
it (a ghost is just "farther"), and Doppler cannot see it (a ghost of a moving
person moves). At 10 fps you still get ~10 samples per stride, which is workable
for cadence.

**Capture `gait` as a second pass on the same scene when gait metrics matter.**
Two 60 s captures back to back cost nothing, and this is the only way to get both
angle and a 25 fps spectrogram — no single config can, because TLV 8 costs 73% of
the link and frame rate is what is left over.

The other two are static-scene tools; reach for them deliberately, not by default.

### Recommended capture order for a session

1. `ghost` + `--label empty-room`, 30 s, nobody in the room. **The only step that
   cannot be added afterwards.**
2. `ghost` for each scene / panel position — the ghost evidence.
3. `gait` for the same scenes if gait numbers are wanted.
4. `doppler` or `survey` only for a deliberate static study.

---

## What the parameters actually control

The command names are TI's (mmWave SDK 3.6, xWR68xx_AOP). Values below are from
`ghost`. **Order matters**: the device applies commands as they arrive, and
`sensorStart` must be last.

### Setup — you should not need to change these

| command | example | what it controls |
|---|---|---|
| `sensorStop` | `sensorStop` | Halts any run already in progress. First line so a re-config cannot collide with a live sensor. |
| `flushCfg` | `flushCfg` | Clears the previous configuration. Without it, leftover settings silently persist. |
| `dfeDataOutputMode` | `1` | `1` = frame-based chirps (the normal mode). `3` = advanced subframes. |
| `channelCfg` | `15 7 0` | Rx mask then Tx mask, as bitfields. `15` = all 4 Rx, `7` = all 3 Tx → **12 virtual antennas**. Reducing either shrinks the radar cube but costs angular resolution. |
| `adcCfg` | `2 1` | 16-bit ADC, complex output. |
| `adcbufCfg` | `-1 0 1 1 1` | ADC buffer: complex, non-interleaved. `-1` = applies to all subframes. |
| `lowPower` | `0 0` | Low-power ADC mode off. |
| `lvdsStreamCfg` | `-1 0 0 0` | High-speed LVDS streaming off. Turning this on is how you would reach raw ADC data — it needs a DCA1000EVM, not UART. |
| `calibData` | `0 0 0` | Do not save/restore calibration to flash. |

### The two that set every derived number

**`profileCfg 0 60 69 7 32 0 0 70 1 128 5209 0 0 158`**

| position | value | meaning |
|---|---|---|
| 1 | `0` | profile id |
| 2 | `60` | **start frequency, GHz** |
| 3 | `69` | **idle time, µs** — dead time between chirps |
| 4 | `7` | ADC valid start time, µs |
| 5 | `32` | **ramp end time, µs** — chirp duration |
| 6–7 | `0 0` | Tx power backoff, phase shifter |
| 8 | `70` | **frequency slope, MHz/µs** |
| 9 | `1` | Tx start time, µs |
| 10 | `128` | **number of ADC samples** |
| 11 | `5209` | **ADC sample rate, ksps** |
| 12–13 | `0 0` | HPF corner frequencies |
| 14 | `158` | Rx gain, dB |

**`chirpCfg 0 0 0 0 0 0 0 1`** (one line per Tx: masks `1`, `2`, `4`)

| position | value | meaning |
|---|---|---|
| 1–2 | `0 0` | start and end chirp index for this entry |
| 3–7 | `0 0 0 0 0` | profile id, then start-freq / freq-slope / idle / ADC-start offsets |
| 8 | `1` | **Tx antenna enable mask** — `1`, `2`, `4` for the three Tx |

Three `chirpCfg` lines plus `frameCfg 0 2 …` is what makes `numTxAnt = 3` and
therefore 12 virtual antennas. It is also why `v_max` carries an `n_Tx` term: the
three Tx fire in sequence, so the effective Doppler sample interval is `3 × Tc`.

**`frameCfg 0 2 96 0 100 1 0`**

| position | value | meaning |
|---|---|---|
| 1–2 | `0 2` | first and last chirp index (3 chirps → one per Tx) |
| 3 | `96` | **number of loops** — chirps per Tx per frame |
| 4 | `0` | number of frames (`0` = stream until stopped) |
| 5 | `100` | **frame periodicity, ms** → 10 fps |
| 6–7 | `1 0` | trigger select (software), trigger delay |

### How those two produce everything else

```
chirp period      Tc      = idle_time + ramp_end_time            = 69 + 32 = 101 µs
range resolution  dR      = c / (2 · slope · ramp_time)          -> 0.0872 m
range bins                = next_pow2(num_adc_samples)           -> 128
max range               = range_bins · dR                        -> 8.93 m
max velocity      v_max   = λ / (4 · Tc · n_Tx)                  -> ±4.07 m/s
doppler bins      N       = next_pow2(num_loops)                 -> 128
velocity res      dV      = 2 · v_max / N                        -> 0.0635 m/s
radar cube                = range_bins · loops · n_virtual · 4   -> 576 KB  (L3 limit 768 KB)
frame rate                = 1000 / frame_periodicity_ms          -> 10 fps
```

Two hard ceilings follow, and they are why "fast and fine" is not available:

* **`v_max` and `dV` pull in opposite directions** unless you raise the Doppler
  bin count — and bins cost radar-cube memory.
* **The radar cube must fit in L3 SRAM (768 KB).** Buying Doppler bins costs
  range bins or virtual antennas.

A third ceiling is the link, not the chip: **the UART carries ~92 KB/s**, and
every enabled payload multiplies by frame rate. That is the whole reason
`gait` can run at 25 fps and `ghost` cannot.

### What gets transmitted — the payload switch

**`guiMonitor -1 1 1 0 1 0 1`**

| position | value | payload | TLV |
|---|---|---|---|
| 1 | `-1` | all subframes | – |
| 2 | `1` | detected point cloud (+ side info) | **1**, 7 |
| 3 | `1` | range profile (log-magnitude) | **2** |
| 4 | `0` | noise profile | 3 |
| 5 | `1` | **range-azimuth heat map** → on AOP this emits the azimuth-**elevation** map | **8** (or 4) |
| 6 | `0` | range-Doppler heat map | **5** |
| 7 | `1` | statistics | 6 |

This line is the single most consequential choice in the file. **Nothing here can
be retrofitted**: if a payload was not enabled at capture time, the bytes were
never transmitted and no amount of reprocessing recovers them.

Measured TLV 8 payload: **3072 bytes/frame** at 64 range bins =
`4 × 12 virtual ant × 64`, decoding to a `(64, 12) complex64` array with zero
parse anomalies.

### On-chip gating — the only place points actually disappear

These run on the radar DSP **before** the UART, so no host-side parser can recover
what they remove.

| command | example | what it controls |
|---|---|---|
| `cfarCfg` (range) | `-1 0 2 8 4 3 0 15 1` | CFAR along range. `15` = **threshold in dB**, final `1` = peak grouping on. Raise the threshold and weak returns vanish. |
| `cfarCfg` (doppler) | `-1 1 0 4 2 3 1 15 1` | Same along Doppler. |
| `cfarFovCfg` (range) | `-1 0 0 8.93` | **Range gate, metres.** Detections outside are dropped on-chip. |
| `cfarFovCfg` (doppler) | `-1 1 -4.07 4.07` | **Doppler gate, m/s.** The TI baseline ships this at ±1.00 m/s, which silently discards every walking limb. |
| `aoaFovCfg` | `-1 -90 90 -90 90` | Azimuth then elevation gate, degrees. Full FoV here. |
| `multiObjBeamForming` | `-1 1 0.5` | **ON, threshold 0.5.** Reports *secondary* angular peaks at ≥50% of the dominant peak in the same range-Doppler bin. |
| `clutterRemoval` | `-1 0` | **OFF** — static returns are kept. |
| `calibDcRangeSig` | `-1 0 -5 8 256` | DC range antenna-coupling removal, off. |
| `extendedMaxVelocity` | `-1 0` | Doppler de-aliasing off. |
| `compRangeBiasAndRxChanPhase` | `0.0 1 0 …` | Range bias (m) and per-antenna phase/gain from calibration. The range bias is subtracted from range-profile and heat-map axes but **not** from the point cloud. |
| `measureRangeBiasAndRxChanPhase` | `0 1.5 0.2` | Calibration measurement mode, off. |
| `CQRxSatMonitor`, `CQSigImgMonitor`, `analogMonitor` | – | Chirp-quality monitors, off. |

Two of these matter directly to the ghost work:

* **`multiObjBeamForming`** is a documented generator of exactly the ghost points
  being classified. Treat it as an **experimental variable, not a default**:
  capture matched pairs at `-1 1 0.5` and `-1 0 0.5`. Points present in the first
  and absent in the second are device-declared secondary peaks by construction.
  This is the cheapest decisive ablation available.
* **`clutterRemoval` off** means most points are static. In the reference
  recording 1262 of 1634 points (77%) had exactly zero Doppler — wall and
  furniture, not the subject.

---

## Per-capture configs are the real provenance

`live.py` copies the exact `.cfg` it sent into every run directory alongside the
`.dat`. **That** copy is the provenance record — always analyse a `.dat` against
the `.cfg` beside it, never one from this directory, because the frame period sets
replay timing and the assumed sample interval for every time-series result.

All 37 existing runs carry theirs, which is why superseding a preset here cost
nothing.

## `xwr68xx_AOP_ti-baseline_10fps_points.cfg` — archival reference

Not generated, not for new captures. A byte-identical copy of a TI Visualizer
**SAVE CONFIG TO PC** export, and **the original config used in the 2026-09-26
demo** — it was reused verbatim from May. Kept because several recordings depend
on it.

SHA-256 `1CF6B2AE6E02592008C5E6FCFFDC305BAF50E43136398A1C6C48B828D11B2AB9`.
Byte-identical to `Downloads/xwr68xx_AOP_profile_2026_09_26T17_23_08_050.cfg` and
to `Downloads/walking_parallel_shorter_length/xwr68xx_AOP_profile_2026_05_06T03_52_25_818.cfg`
(previously filed here as `xwr68xx_AOP_10fps.cfg`).

Used by `run_20260926_100931`, `run_20260926_102437` and `run_20260926_103456`.

| | |
|---|---|
| Platform / SDK | xWR68xx_AOP, mmWave SDK 3.6 |
| Scene classifier | `best_range_res`, 60–64 GHz |
| Antennas | 3 Tx × 4 Rx = 12 virtual |
| Frame rate | 10 fps |
| Range | 256 bins, 0.0436 m resolution, 8.93 m max |
| Doppler | 16 bins, 0.1217 m/s resolution, **±0.97 m/s max** |
| Payloads | point cloud, range profile, stats (`guiMonitor -1 1 1 0 0 0 1`) |

**Do not use it for new work.** Its ±0.97 m/s ceiling aliases every walking limb —
a hand at 2.4 m/s folds back to a wrong velocity — and `cfarFovCfg -1 1 -1 1.00`
enforces that gate on-chip. This is the config that was hiding the micro-Doppler.
It has the best range resolution of anything here (0.0436 m), which is the one
reason to keep it around.
