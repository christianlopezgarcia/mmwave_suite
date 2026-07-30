# mmwave_analysis

Analysis, parsing and figure generation for 60 GHz mmWave radar sensing.
Replaces `mmwave_sensing_toolkit`. **Read-only by design** — this repo never
talks to hardware. Recording lives in `mmwave_direct`.

Aligned to the group's own paper — Ganditi, Chilakala, Najafi, Eltayeb & Smith,
*Millimeter-Wave Body-Centric Radar Sensing for Continuous Monitoring of Human
Gait Dynamics* — whose pipeline (Fig. 2, Eqs. 1–6) this implements.

---

## Structure

```
mmwave_analysis/
├── core/                  acquisition-agnostic foundation
│   ├── recording.py       Recording, Geometry, OnChipGating
│   ├── io.py              .dat + .cfg -> Recording (parsing via mmwave_direct)
│   ├── maps.py            RT / VT construction, Eq. (1)
│   └── clutter.py         empty-scene subtraction, zero-Doppler notch
├── micro_doppler/         gait dynamics
│   ├── envelope.py        foot-velocity envelope, Eq. (2) median+MAD
│   ├── steps.py           walking segments (hysteresis), step events
│   └── gait.py            cadence, step time, CV, MAPE — Eqs. (3)–(6)
├── ghost_sections/        multipath-aware detection
│   ├── features.py        per-point features grounded in specular geometry
│   └── detectors.py       pluggable methods + registry
├── plotting/
│   ├── maps.py            RT/VT figures in the paper's conventions
│   ├── gait.py            envelope, step markers, interval sequence
│   └── ghosts.py          raw-vs-filtered comparisons
├── pipelines/run.py       the single CLI entry point
├── archive/               legacy code, kept functional, not on the import path
│   ├── legacy_parsers/    dat_parser_plots{,_v2,_v3}, compare_radar*
│   ├── ti_mmw_official_tool/  TI's reference parser (BSD-3)
│   └── reference_matlab/  run_fixed_two_person.m, convert_to_mat.py
└── Plots/
    ├── raw_unfiltered/<run>/   baseline, filtering bypassed entirely
    ├── <method_name>/<run>/    one folder per ghost method
    └── _comparison/<run>/      cross-method agreement
```

Every output folder gets a `manifest.json`: parameters, fidelity tier, integrity
counters, metrics, and the provenance string of each map. A figure without a
manifest is not reproducible.

---

## Fidelity tiers — read this before interpreting any result

The paper's pipeline starts from **raw complex ADC samples**. The TI demo does
not emit those over UART. What you get depends on `guiMonitor`:

| Tier | Source | RT | VT |
|---|---|---|---|
| **A** | raw ADC (LVDS/DCA1000) | exact | exact |
| **B** | range-Doppler map, TLV 5 | Eq. (1) verbatim | Eq. (1) verbatim |
| **C+** | range profile TLV 2 + point cloud | **dense, real** | sparse |
| **C** | point cloud only | sparse | sparse |

Your current captures are **C+**. The RT map is genuinely dense and comes from
the measured range spectrum. The VT map is a *sparse reconstruction* from
CFAR-detected points — sub-threshold micro-Doppler was discarded on the chip and
cannot be recovered. `Recording.tier` reports this, and every map carries a
`provenance` string that is stamped into the figure.

**Why not just enable TLV 5?** Bandwidth. A 128×128 RD map is 32 KB/frame; at
20 fps that is 655 KB/s against a 921600-baud UART's ~92 KB/s ceiling — 7× over.
Tier B needs a deliberately reduced config (≈64×64 at ≤8 fps). See
`docs/FIDELITY.md`.

---

## Usage

```bash
# baseline first, always
python -m mmwave_analysis.pipelines.run --dat RUN.dat --raw

# one method
python -m mmwave_analysis.pipelines.run --dat RUN.dat --method method4_mirror_geometry

# all methods + agreement matrix
python -m mmwave_analysis.pipelines.run --dat RUN.dat --all-methods

# gait only, with a range ROI (paper §2.3 gating)
python -m mmwave_analysis.pipelines.run --dat RUN.dat --gait --roi 1.0 7.0

python -m mmwave_analysis.pipelines.run --list-methods
```

`--cfg` is inferred from the `.cfg` beside the `.dat`; the loader **refuses to
guess** when there is more than one, because a mismatched `.cfg` silently
rescales every axis.

---

## Ghost methods

| Method | Basis |
|---|---|
| `method0_none` | baseline, nothing removed |
| `method1_mti_filter` | near-zero-Doppler removal — **clutter control, not a ghost test** |
| `method2_cocell_secondary` | `multiObjBeamForming` secondary angular peaks |
| `method3_snr_range_anomaly` | SNR deficit after removing the 1/R⁴ trend |
| `method4_mirror_geometry` | farther + Doppler-correlated + angularly displaced |
| `method5_consensus` | ≥2 of the three independent tests |

Adding one is a decorated function:

```python
@register("method6_myidea", "what it does")
def method6_myidea(pf, geom, **kw) -> GhostLabels:
    ...
```

Physical basis (`ghost_sections/features.py`): a specular return travels
radar → wall → target → radar, so it is **always farther** than its parent,
arrives from the **reflection point** rather than the target, and pays an
**extra bounce loss**. Those three are the discriminators.

**Empirical note.** On `run_20260725_092656`, MTI showed **0.00** Jaccard
overlap with `method4_mirror_geometry`. That is the design claim confirmed: a
ghost of a *moving* person is itself moving, so MTI cannot touch it. Any method
that appears to beat MTI should be checked for merely removing more static.

---

## Validation

There is **no labelled ghost ground truth**, so no method here is validated —
they are physically-motivated hypotheses. Two things partially compensate:

- `agreement_matrix()` — where methods disagree is where to look.
- `cadence_from_spectrum()` — an independent cadence estimate. Peak-picking and
  spectral estimation fail differently; large disagreement means the step
  detector's thresholds are wrong. On the test run they disagreed by 21%, with
  spectral confidence 0.02, so **those gait numbers are not yet trustworthy**.

The controlled experiment that would settle the ghost question: capture the same
scene twice, `multiObjBeamForming -1 1 0.5` and `-1 0 0.5`. Points present in the
first and absent in the second are device-declared secondary peaks by
construction.

---

## Archive

`archive/legacy_parsers/` keeps `dat_parser_plots_v2.py` and friends fully
functional, with `ti_mmw_official_tool/` alongside so their imports resolve:

```bash
cd archive/legacy_parsers && python dat_parser_plots_v2.py
```

Kept for reference and reproducing earlier figures. Note TI's
`parser_mmw_demo.py` treats a zero-detection frame as a fatal error and reads
the int16 SNR as unsigned — see `../mmwave_direct/README.md`. Do not build new
work on it.
