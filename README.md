# mmwave_suite

One repository for 60 GHz mmWave radar: config design, capture, parsing, analysis
and figures.

```
mmwave_suite/
├── extraction/          talks to the radar
│   ├── link.py  live.py  viz.py        serial control + capture
│   ├── tlv.py  cfgparse.py  audit.py   decoding + integrity
│   ├── design.py                       chirp designer
│   ├── highfidelity/                   bandwidth calc + heat-map configs
│   ├── cfg/                            config library
│   └── ARCHITECTURE.md                 TI reverse-engineering reference
└── analysis/            never touches hardware
    ├── core/            Recording, RT/VT maps, clutter, angle beamforming
    ├── micro_doppler/   envelope, steps, gait metrics, tracking
    ├── ghost_sections/  per-point features, pluggable detectors
    ├── plotting/        maps, gait, ghosts, pointcloud, diagnostics
    └── pipelines/run.py the analysis entry point
```

Anything that can change *what gets recorded* lives in `extraction/`; anything
that only *interprets* a recording lives in `analysis/`. That is what makes a
capture auditable after the fact.

All commands run from `EEE_500\repo`.

---

## 1. Quick reference

```powershell
# record, with a description
python -m mmwave_suite.extraction.live --cli COM4 --data COM5 `
  --cfg mmwave_suite\extraction\cfg\xwr68xx_AOP_rdmap-balanced.cfg `
  --out .\runs --seconds 60 --label "behind plywood 3m"

# check integrity BEFORE analysing
$latest = (Get-ChildItem .\runs -Recurse -Filter *.dat | Sort-Object LastWriteTime -Desc | Select -First 1).FullName
python -m mmwave_suite.extraction.audit $latest

# analyse the newest recording
python -m mmwave_suite.analysis.pipelines.run --latest --raw --all-methods
```

Other captures:

```powershell
# TLV 8 angle data — for ghosts. Fully wired: adds range_azimuth_frame
# and AT_dense_following, which no other config produces.
--cfg mmwave_suite\extraction\cfg\xwr68xx_AOP_angle-ghost.cfg

# empty room — same geometry as angle-ghost, nobody present
--cfg mmwave_suite\extraction\cfg\xwr68xx_AOP_empty-room.cfg --seconds 30
```

---

## 2. Recording

```powershell
python -m mmwave_suite.extraction.live --list-ports
```

**COM4 = CLI** (Enhanced COM Port), **COM5 = DATA** (Standard). COM3 is Intel AMT
Serial-over-LAN — never select it. **Close the TI Visualizer first**; it holds
both ports while connected.

| flag | meaning |
|---|---|
| `--label "text"` | description appended to the run name (alias `--desc`) |
| `--seconds N` | stop after N seconds (0 = until Ctrl-C) |
| `--name X` | override the whole run name, ignoring `--label` |
| `--out DIR` | where run folders go (default `./runs`) |

`--label "behind plywood 3m"` → `runs\run_20260729_203433_behind-plywood-3m\`

Timestamp stays first so runs sort chronologically and `--latest` keeps working.
The slug is lossy, so your **verbatim text is also stored as `label` in
`meta.json`**.

Each run produces `<run>.dat` (raw UART bytes, replays in the TI Visualizer too),
`<run>.cfg`, `<run>.meta.json`, `<run>.idx.jsonl` (per-frame host timestamp,
frame number, byte offset, point count).

---

## 3. Analysing

```powershell
python -m mmwave_suite.analysis.pipelines.run --latest --raw --all-methods
```

| flag | meaning |
|---|---|
| `--latest` | newest `.dat` under `--runs-root`; prints which, and its timestamp |
| `--runs-root DIR` | where `--latest` searches (default `runs`) |
| `--dat PATH` | a specific recording |
| `--cfg PATH` | explicit config (otherwise the one beside the `.dat`) |
| `--raw` | unfiltered baseline → `Plots/raw_unfiltered/` |
| `--all-methods` | every ghost method + agreement matrix |
| `--method NAME` | one method (`--list-methods` to list) |
| `--gait` | gait only, no ghost tagging |
| `--roi MIN MAX` | range gate in metres |
| `--plots-root DIR` | aggregate figures elsewhere instead of beside the data |

---

## 4. The graphs — what each one is and why it exists

Output layout per run:

```
runs/<run>/Plots/
├── raw_unfiltered/            NO filtering — the baseline
│   ├── <dense maps + gait>
│   └── point_cloud_views/     13 per-detection figures
├── method4_mirror_geometry/   same figures, that method's filtering applied
└── _comparison/               cross-method agreement
```

**Always read a filtered figure next to its `raw_unfiltered/` twin.** A filter
that removes 80 % of points looks impressive until you notice it removed the
target too.

### 4a. Two families, two questions

| | **dense maps** (`plotting/maps.py`) | **point-cloud views** (`plotting/pointcloud.py`) |
|---|---|---|
| shows | every range-Doppler **cell** | only points the radar **decided** were targets |
| includes | signal CFAR rejected | nothing below threshold |
| carries | power per cell | per-detection SNR, azimuth, elevation |
| density | continuous | ~5 points/frame |
| needs | tier B (TLV 5) to be meaningful in Doppler | any capture |

A dense map cannot tell you *"the detector fired here at 28 dB SNR, 40° azimuth"*.
A point cloud cannot tell you *"there was energy here 2 dB below threshold"*.
Both are kept because neither is sufficient.

### 4b. Dense maps

**`RT_dense.png` — Range-Time.** Range on Y, time on X, colour = power. Built
from TLV 2, a *measured* range spectrum. Horizontal lines are static clutter
(walls, furniture); diagonal/V-shaped tracks are moving targets. Your primary
"who was where, when" figure.
*It cannot be ghost-filtered* — it is a measured spectrum, not derived from
points, so a point-level mask has no effect on it. Never labelled "filtered".

**`RT_dense_baseline_removed.png`** — same map with each range row's median
subtracted over time. **Filtering technique: temporal median subtraction.** A
wall is constant in range so its row median is high and it cancels; a walking
person is not, so they survive. A pragmatic stand-in for empty-scene subtraction
when no empty recording exists. Weaker than the real thing, because it works on
magnitudes after the FFT rather than coherently before it.

**`RT_dense_with_track.png`** — RT with the fitted target track overlaid in
white. Use it to check the tracker actually followed the person. **If the white
line is flat, the track locked onto clutter and every downstream VT is wrong.**

**`RT_points.png`** — RT rebuilt from detected points, SNR-weighted. Sparser than
the dense version, but it **does** respond to ghost filtering, so this is the RT
to use when comparing methods.

**`VT_following.png` — Velocity-Time, target-following.** The important one.
Velocity on Y, time on X. Per frame, only a ±0.25 m window around the tracked
range is Doppler-summed — the reference paper's `dyn_gate_halfwidth_m`.
Read it as: the smooth ridge oscillating ±1 m/s is the **torso**, its sign
flipping at each turnaround; the spikes to ±3 m/s are **limbs**; the bright line
at 0 m/s is static clutter.

**`VT_fixed_roi_compare.png`** — same data with a *fixed* range gate. Kept
deliberately so you can see the difference: a fixed ROI integrates every clutter
source in the gate for the whole recording, which is why it looks muddy. This
comparison is the argument for target-following, made visible rather than
asserted.

**`RT_VT_pair.png`** — RT above VT, shared time axis. The paper's Fig. 3/4 layout.

### 4c. Gait figures

**`envelope_steps.png`** — the foot-velocity envelope *v*_foot(*t*) with detected
steps (red) and walking segments (green bands).
**Filtering technique: median + MAD masking.** Per frame, keep only Doppler bins
above `median + 3·MAD` of that frame's column, then take the 98th percentile of
|v| among them. MAD rather than standard deviation because one strong torso
return would otherwise inflate the threshold and mask out the limb bins the
envelope is meant to capture. Walking segments come from **hysteresis** (on at
0.35 m/s, off at 0.20) — a single threshold chatters at every stance phase and
shreds one walk into many fragments.

**`step_intervals.png`** — Δt between consecutive steps vs step index, with
mean ±1 sd. Reveals what a single CV number hides: uniform jitter (genuinely
irregular gait) versus a few outliers (missed detections).

### 4d. Point-cloud views — `point_cloud_views/`

Ported from `dat_parser_plots_v2`, with two fixes: they now honour the ghost mask,
and SNR is decoded as **signed** int16 (TI's reference parser reads it unsigned,
turning a negative SNR into ~6553 dB).

| figure | what it shows | why |
|---|---|---|
| `micro_doppler.png` | time-velocity density, `PowerNorm(0.4)` | point-cloud VT analogue. Gamma compression keeps sparse limb detections visible beside the dense zero-Doppler band, which would otherwise saturate everything |
| `range_time_intensity.png` | time-range density, **log** colour | log because a wall yields orders of magnitude more detections in one bin than a person does across many |
| `azimuth_time_intensity.png` | time-**azimuth** density | **no dense equivalent exists.** A specular ghost sits at a *different bearing* from its parent — a mirrored pair of angular tracks is the signature |
| `elevation_time_intensity.png` | time-**elevation** density | a floor- or ceiling-bounce ghost is displaced in elevation, not azimuth |
| `snr_vs_time.png` | per-detection SNR | sanity-check the CFAR threshold. A cloud hugging the lower edge means the threshold is doing most of the work and weak multipath is being cut |
| `range_vs_time.png` | raw range scatter | clearest view of who moved where |
| `velocity_vs_time.png` | raw velocity, colour-coded | sign reversal at turnarounds |

Two-panel comparisons (shared time axis):

| figure | why this pairing |
|---|---|
| `compare_velocity_range.png` | reads a walk cycle directly — velocity crosses zero exactly where range reverses |
| `compare_snr_range.png` | **ghost-hunting view.** A genuine multipath return is *both farther and weaker*, so look for the panels moving oppositely |
| `compare_micro_doppler_range.png` | ties a limb-velocity burst to the range the subject was at |
| `compare_azimuth_range.png` | **the most useful for ghosts.** Target and specular image share a time signature but sit at different bearings: two angular tracks, one range trajectory |
| `compare_elevation_range.png` | same, for floor/ceiling bounces |
| `compare_rti_range.png` | the same range data as density *and* as scatter. The density map hides how few points make a faint track; the scatter hides how intense a bright one is. Together they stop you over-reading a heat map built from a handful of detections |

### 4e. TLV 4/8 angle figures — **the ghost-critical ones**

**These appear ONLY if you captured with `angle-ghost`, `empty-room` or
`both-survey`.** `rdmap-balanced` does *not* emit TLV 8, so with your current
recordings these figures are absent. The pipeline prints
`angle_iq (TLV 4/8)  -- absent` when that is the case.

Why they are different from everything else in the repo: TLV 4/8 is the **only
payload TI ships before its FFT**. It is raw complex I/Q, one sample per virtual
antenna, per range bin, at zero Doppler. The angle transform is left to the host,
so `analysis/core/angle.py` does the beamforming — meaning the angular processing
is *yours to change*, unlike range and Doppler which are fixed in silicon.

That matters because a specular ghost is **defined by arriving from the wrong
direction**. Range cannot see it (a ghost is just "farther"). Doppler cannot see
it (a ghost of a moving target moves). Bearing can — and bearing is the one axis
still under your control.

**`range_azimuth_frame.png`** — two panels for one frame.
*Left:* range (Y) vs azimuth (X), raw beamformer output. *Right:* the same data
resampled to Cartesian X/Y — a bird's-eye view of the room, the way the TI
Visualizer draws its plot4.
How to read it: **two bright returns at the same range but different bearing is
direct ghost evidence.** One physical scatterer cannot occupy two bearings. The
nearer-bearing return is normally the target; the offset one is its image in a
wall.
Caveat: the azimuth axis is **non-linear**. Angle bin *k* maps to
sin θ = 2*k*/N, so θ = asin(2*k*/N) — resolution is finest at broadside and
degrades toward the edges. That is a property of a linear array, not a plotting
choice. Do not read edge separations as though they were centre separations.

**`AT_dense_following.png` — dense Azimuth-Time.**
The signal-domain twin of `point_cloud_views/azimuth_time_intensity.png`.
Same axes, completely different provenance:

| | point-cloud version | this one |
|---|---|---|
| built from | detections that passed CFAR | **beamformed signal** |
| a weak ghost | invisible if below threshold | **still visible** |
| density | ~5 marks per frame | every angle bin, every frame |

This is the figure where a multipath image that never passed CFAR shows up
anyway. Integrated over a ±0.5 m window that follows the tracked target, so the
wall's own bearing is kept out of the sum.
How to read it: a single walking person gives **one** angular track that drifts
smoothly. **Two tracks moving in sympathy — mirrored about some fixed bearing —
is the multipath signature.** The fixed bearing they mirror about tells you where
the reflecting surface is.

Also available programmatically, no figure yet:
`core/angle.angular_spread(rec, frame, range_m)` returns the beam pattern at one
range in one frame. Two peaks in that 1-D cut is the cleanest single-frame ghost
evidence obtainable — worth plotting by hand for a specific moment you want to
argue about in the thesis.

### 4f. Payload diagnostics — `payload_diagnostics/`

These describe the **capture**, not the scene, so they are identical across ghost
methods and are written only for `raw_unfiltered/`.

**`range_noise_profile.png`** *(needs TLV 3 — see §4h, currently off in all configs)*
Top: range profile (mean and one frame) with the noise profile overlaid.
Bottom: **signal − noise per range bin** — the CFAR margin.
Why it matters more than it looks: the margin panel separates *"nothing was
there"* from *"something was there and the threshold rejected it."* Bins hovering
near zero are exactly where a weak multipath return would be lost. If you ever
need to argue that a ghost was suppressed rather than absent, this is the figure
that supports it.

**`processing_stats.png`** *(TLV 6 — present in every config)*
Top: DSP active-frame and interframe CPU load. Bottom: **interframe processing
margin in ms**, with the minimum annotated.
Why it matters: when the margin trends toward zero the device is about to start
dropping frames, and **the capture log will not tell you.** A run that reports a
clean 8.00 fps can still be one configuration change away from tearing. If the
title says `AT RISK OF DROPPING FRAMES`, lower the frame rate before trusting the
data.

**`temperature.png`** *(TLV 9 — present in every config)*
Rx/Tx/PM/digital die temperatures over the capture.
Why it matters: the 60 GHz front end drifts with die temperature. A slow SNR
decline across a 10-minute run is more often thermal than physical — this figure
is how you tell the difference before writing it up as a finding.

### 4g. Which figures you actually get, per config

| figure | needs | `rdmap-*` | `angle-ghost` / `empty-room` | `both-survey` | `microdoppler*`, `10fps` |
|---|---|---|---|---|---|
| `RT_dense`, `RT_points`, `RT_*_with_track` | TLV 2 | yes | yes | yes | yes |
| all 13 `point_cloud_views/` | TLV 1+7 | yes | yes | yes | yes |
| `processing_stats`, `temperature` | TLV 6/9 | yes | yes | yes | yes |
| `envelope_steps`, `step_intervals` | TLV 1 | yes | yes | yes | yes |
| all ghost method figures | TLV 1+7 | yes | yes | yes | yes |
| **`VT_following` as a DENSE map** | **TLV 5** | **yes** | no — sparse | **yes** | no — sparse |
| **`range_azimuth_frame`** | **TLV 4/8** | **no** | **yes** | **yes** | no |
| **`AT_dense_following`** | **TLV 4/8** | **no** | **yes** | **yes** | no |
| `range_noise_profile` bottom panel | TLV 3 | **no** | **no** | **no** | **no** |

Nothing is ever silently skipped — the pipeline prints a payload table at the top
of every run:

```
Payloads present in this recording:
  point_cloud (TLV 1)        yes
  range_profile (TLV 2)      yes
  noise_profile (TLV 3)      -- absent
  angle_iq (TLV 4/8)         -- absent
  range_doppler (TLV 5)      yes
  ...
```

### 4h. TLV 3 (noise profile) is off in every config — deliberately, for now

`guiMonitor` field 4 is `0` in all ten configs, so the CFAR-margin panel is
empty. Enabling it costs almost nothing in bytes (2 × numRangeBins per frame) but
it does push the heat-map presets from 73–75 % to **76–77 %** of the UART, just
past the 75 % safety line, or costs ~0.5 fps to stay inside it:

| preset | without TLV 3 | with TLV 3 | fps to stay ≤75 % |
|---|---|---|---|
| `angle-ghost` | 73 % | 76 % | 10.0 → 9.9 |
| `rdmap-balanced` | 75 % | 76 % | 8.0 → 7.9 |
| `rdmap-fast` | 75 % | 77 % | 15.2 → 14.8 |
| `both-survey` | 64 % | 64 % | fits as-is |

**Not enabled by default because you have already captured with these configs**,
and changing one after the fact makes new recordings non-comparable with old
ones. To add it, edit the preset in
`extraction/highfidelity/configs.py` (`gui_noise_profile=1`), nudge
`frame_periodicity_ms` up by ~2 %, and regenerate — `write_all()` refuses to emit
anything over budget, so it will stop you if it does not fit.

### 4i. Ghost figures (per method)

**`before_after.png`** — 2×2: range and velocity, raw on top, filtered below.
The single most important diagnostic. If a real trajectory disappears between
rows, the method is over-filtering and no summary statistic will tell you.

**`range_time_tagged.png`** — kept points blue, tagged points red ×.

**`feature_space.png`** — the three discriminating features with tagged points
highlighted. If the tagged cloud is not separated in *any* panel, the criterion
is arbitrary rather than physical.

**`_comparison/method_comparison.png`** — % tagged per method plus pairwise
Jaccard overlap.

---

## 5. Filtering techniques, and where each acts

| technique | where | what it removes | reversible? |
|---|---|---|---|
| CFAR thresholding | **radar DSP** | everything below ~15 dB | **NO — never transmitted** |
| peak grouping | **radar DSP** | adjacent detections merged | **NO** |
| FoV gating (`cfarFovCfg`, `aoaFovCfg`) | **radar DSP** | outside range/Doppler/angle limits | **NO** |
| `multiObjBeamForming` | **radar DSP** | *adds* secondary angular peaks | n/a — **ghost generator** |
| zero-Doppler notch (MTI) | host | static returns | yes |
| temporal median subtraction | host | time-invariant clutter | yes |
| empty-scene subtraction | host | measured background (needs TLV 5 + empty run) | yes |
| median+MAD mask | host | per-frame noise floor in VT | yes |
| ghost methods 2–5 | host | tagged points | yes |

**The top four are permanent.** They happen before anything reaches the host, so
no reprocessing recovers them. That is why the config matters more than the
analysis.

### Ghost methods

| method | basis |
|---|---|
| `method0_none` | baseline |
| `method1_mti_filter` | near-zero-Doppler removal — **clutter control, not a ghost test** |
| `method2_cocell_secondary` | `multiObjBeamForming` secondary angular peaks |
| `method3_snr_range_anomaly` | SNR deficit after removing the 1/R⁴ trend |
| `method4_mirror_geometry` | farther + Doppler-correlated + angularly displaced |
| `method5_consensus` | ≥2 of the three independent tests |

Measured on `run_20260729_203433`, MTI shows **0.00** Jaccard overlap with
`method4_mirror_geometry` — confirming that a ghost of a *moving* person is
itself moving, so MTI cannot touch it. Any method appearing to beat MTI should be
checked for merely removing more static.

**No method is validated** — there is no labelled ground truth. They are
physically-motivated hypotheses. The controlled experiment: capture the same
scene twice, `multiObjBeamForming -1 1 0.5` and `-1 0 0.5`.

---

## 6. The config library

`link%` is the share of the 921600-baud UART consumed. Above ~75 % the link has
no slack and the device tears frames.

| config | rng | dop | fps | rng res | v res | v max | TLV | link% |
|---|---|---|---|---|---|---|---|---|
| **`angle-ghost`** | 128 | 128 | 10.0 | 0.087 | 0.064 | ±4.07 | **8** | 73 % |
| **`empty-room`** | 128 | 128 | 10.0 | 0.087 | 0.064 | ±4.07 | **8** | 73 % |
| `rdmap-balanced` | 64 | 64 | 8.0 | 0.174 | 0.127 | ±4.06 | **5** | 75 % |
| `rdmap-fast` | 64 | 32 | 15.2 | 0.174 | 0.254 | ±4.06 | **5** | 75 % |
| `both-survey` | 64 | 64 | 5.0 | 0.174 | 0.127 | ±4.06 | **5+8** | 64 % |
| `limb-separation` | 128 | 128 | 20.0 | 0.087 | 0.064 | ±4.07 | – | 12 % |
| `microdoppler-fast` | 128 | 64 | 25.0 | 0.087 | 0.127 | ±4.07 | – | 16 % |
| `microdoppler` | 128 | 64 | 10.0 | 0.087 | 0.127 | ±4.07 | – | 6 % |
| `microdoppler-fine` | 128 | 128 | 10.0 | 0.087 | 0.064 | ±4.07 | – | 6 % |
| `10fps` | 256 | 16 | 10.0 | 0.044 | 0.122 | **±0.97** | – | 9 % |

**`angle-ghost` — TLV 8, complex I/Q per virtual antenna.** The only payload TI
ships **pre-FFT**, so you can do your own beamforming — and a specular ghost is
*defined* by arriving from the wrong direction. Costs nothing in range or Doppler
resolution: TLV 8 is a zero-Doppler slice, so its size has no `numDopplerBins`
term. You pay 20 → 10 fps and nothing else. Fully consumed: see §4e.

**`rdmap-balanced` / `rdmap-fast` — TLV 5, dense range-Doppler, pre-CFAR.**
Auto-upgrades the recording to **tier B**: RT/VT become the paper's Eq. (1)
verbatim, every Doppler bin populated instead of ~1.3, and empty-scene
subtraction becomes possible. **No angle information** — reveals weak multipath
CFAR was discarding, but cannot discriminate a ghost.

**`both-survey`** — everything at 5 fps. Static-scene study, not gait.

**`limb-separation` / `microdoppler*`** — point cloud only, tier C+: dense RT but
a **sparse VT** (~1.3 of 128 bins populated). Fast, cheap on bandwidth.

**`10fps` — the old baseline. Do not use for new work.** Its ±0.97 m/s ceiling
aliases every walking limb: a hand at 2.4 m/s folds back to a wrong velocity.
This is the config that was hiding your micro-Doppler.

### Recommended order

1. **`rdmap-balanced`** — the one change that measurably improves what you
   already analyse. Tier B, dense VT, Eq. (1) maps, working today.
2. **`empty-room`** immediately after, before anything in the room moves. 30 s,
   nobody present. **The only step that cannot be added later.**
3. **`angle-ghost`** — the config your thesis needs. Now fully wired (§4e).
4. `microdoppler-fast` (25 fps) if you want time resolution and can accept a
   sparse VT.

---

## 7. No `.cfg`? What happens

If no `.cfg` sits beside the `.dat` and you pass no `--cfg`, the loader falls back
in this order and prints a loud banner:

1. `xwr68xx_AOP_10fps.cfg` ← **the default**
2. `xwr68xx_AOP_limb-separation.cfg`
3. `xwr68xx_AOP_microdoppler.cfg`

`cfg_is_fallback` is recorded in the `Recording` and every `manifest.json`.

**It will run. It will not necessarily be right.** Measured, parsing a real
64 × 64 tier-B capture with that 256 × 16 fallback:

| | correct cfg | fallback cfg |
|---|---|---|
| range bins | 64 (0.174 m) | **256 (0.044 m)** — 4× too fine |
| max velocity | ±4.06 m/s | **±0.97 m/s** — 4× too small |
| duration | 60.1 s | **48.1 s** — fps wrong |
| range profile | 0 % NaN | **75 % NaN** |
| anomalies | none | **482 bin-count mismatches** |

Two of those are loud. One is **not**: because 64 × 64 and 256 × 16 both contain
4096 cells, the range-Doppler map passes its size check and is silently reshaped
to the wrong dimensions. A scrambled RD map still renders as a plausible figure.

**So audit any recording whose config you are unsure of:**

```powershell
python -m mmwave_suite.extraction.audit "<.dat>" --cfg "<candidate .cfg>"
```

Look for `Range-bin cross-check: ... -> MATCH`. Old recordings are still fully
usable — just pair each with the config it was captured under, and every existing
run has its `.cfg` sitting beside it.

---

## 8. Design tools

```powershell
python -m mmwave_suite.extraction.design --compare            # chirp trade-offs
python -m mmwave_suite.extraction.highfidelity.bandwidth      # UART budget survey
python -m mmwave_suite.extraction.highfidelity.configs        # heat-map presets
```

`design.py` reports Doppler **bin spacing** and **true (CPI-limited) resolution**
separately. They differ when the FFT is zero-padded, and only the second governs
whether two scatterers can actually be separated.

---

## 9. Known limits

- **Gait metrics are not yet trustworthy.** Cadence agrees with the independent
  spectral estimate to 4 %, but CV ≈ 78 % and spectral confidence 0.02. The step
  detector needs calibrating against a walk where you counted the steps.
- **Raw ADC is impossible over UART** — 11.8 MB/s against a 92 KB/s link, 128×
  over. Needs LVDS + a DCA1000EVM.
- **Coherent (ADC-domain) background subtraction** is out of reach; the
  implemented empty-scene subtraction works on RD magnitudes, weaker than the
  reference MATLAB's pre-FFT cancellation.
- **This repo is not under version control.** The sibling `repo/mmwave_analysis`
  lost a session of uncommitted work to a discarded changeset; `mmwave_suite`
  currently holds the only copy of several fixes.
