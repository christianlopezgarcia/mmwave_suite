# mmwave_suite.analysis

Analysis, parsing and figure generation for 60 GHz mmWave radar sensing.
**Read-only by design** — nothing here talks to hardware. Recording and config
design live in [extraction/](../extraction/).

That split is load-bearing, not cosmetic: anything that can change what is
recorded lives in `extraction/`, anything that only interprets what was already
recorded lives here. It is what makes a recording auditable after the fact —
analysis can never silently alter the evidence.

Aligned to the group's own paper — Ganditi, Chilakala, Najafi, Eltayeb & Smith,
*Millimeter-Wave Body-Centric Radar Sensing for Continuous Monitoring of Human
Gait Dynamics* — whose pipeline (Fig. 2, Eqs. 1–6) this implements.

**Run every command below from the directory that contains `mmwave_suite/`.**

---

## Quickstart

```bash
# baseline first, always: no filtering at all
python -m mmwave_suite.analysis.pipelines.run --latest --raw

# same thing for a specific recording
python -m mmwave_suite.analysis.pipelines.run --dat runs/RUN/RUN.dat --raw

# micro-Doppler / gait, gated to a range ROI (paper §2.3)
python -m mmwave_suite.analysis.pipelines.run --latest --gait --roi 1.0 7.0

# one ghost method
python -m mmwave_suite.analysis.pipelines.run --latest --method method4_mirror_geometry

# every method, plus the cross-method agreement figure
python -m mmwave_suite.analysis.pipelines.run --latest --all-methods

python -m mmwave_suite.analysis.pipelines.run --list-methods
```

| flag | what it does |
|---|---|
| `--dat PATH` | the recording to analyse |
| `--latest` | use the most recent `.dat` under `--runs-root` instead |
| `--runs-root DIR` | where `--latest` searches (default `runs`) |
| `--cfg PATH` | matching `.cfg` (default: the one beside the `.dat`) |
| `--raw` | unfiltered baseline → `Plots/raw_unfiltered/` |
| `--method NAME` | one ghost method → `Plots/<name>/` |
| `--all-methods` | every method + `Plots/_comparison/` |
| `--gait` | micro-Doppler / gait figures and metrics |
| `--roi MIN MAX` | range gate in metres |
| `--v-min V` | minimum speed treated as motion |
| `--plots-root DIR` | aggregate figures here instead of beside the recording |
| `--list-methods` | print the method registry and exit |

`--cfg` is inferred from the `.cfg` beside the `.dat`; the loader **refuses to
guess** when there is more than one, because a mismatched `.cfg` silently
rescales every axis. `extraction/live.py` writes that `.cfg` into every run
directory, so this normally just works.

---

## Structure

```
analysis/
├── core/                  acquisition-agnostic foundation
│   ├── recording.py       Recording, Geometry, OnChipGating
│   ├── io.py              .dat + .cfg -> Recording (parsing via ../extraction)
│   ├── maps.py            RT / VT construction, Eq. (1)
│   ├── angle.py           TLV 4/8 angle I/Q -> beamformed azimuth maps
│   └── clutter.py         empty-scene subtraction, zero-Doppler notch
├── micro_doppler/         gait dynamics
│   ├── envelope.py        foot-velocity envelope, Eq. (2) median+MAD
│   ├── steps.py           walking segments (hysteresis), step events
│   ├── gait.py            cadence, step time, CV, MAPE — Eqs. (3)–(6)
│   └── tracking.py        target range track, for target-following VT
├── ghost_sections/        multipath-aware detection
│   ├── features.py        per-point features grounded in specular geometry
│   └── detectors.py       pluggable methods + registry
├── plotting/
│   ├── maps.py            RT/VT figures in the paper's conventions
│   ├── gait.py            envelope, step markers, interval sequence
│   ├── ghosts.py          raw-vs-filtered comparisons
│   ├── pointcloud.py      per-point scatter/density vs time
│   └── diagnostics.py     integrity, SNR, TLV composition panels
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
manifest is not reproducible, and a thesis figure that is not reproducible is a
liability.

---

## Fidelity tiers — read this before interpreting any result

The paper's pipeline starts from **raw complex ADC samples**. The TI demo does
not emit those over UART. What you get depends on what `guiMonitor` enabled *at
capture time*:

| Tier | Source | RT | VT |
|---|---|---|---|
| **A** | raw ADC (LVDS/DCA1000) | exact | exact |
| **B** | range-Doppler map, TLV 5 | Eq. (1) verbatim | Eq. (1) verbatim |
| **C+** | range profile TLV 2 + point cloud | **dense, real** | sparse |
| **C** | point cloud only | sparse | sparse |

Most captures to date are **C+**. The RT map is genuinely dense and comes from
the measured range spectrum. The VT map is a *sparse reconstruction* from
CFAR-detected points — sub-threshold micro-Doppler was discarded on the chip and
cannot be recovered. `Recording.tier` reports this, and every map carries a
`provenance` string stamped into the figure.

**This is honest by construction.** A point-cloud ghost decision cannot filter a
measured TLV-5 spectrum, so passing a `keep_mask` to `velocity_time_map()`
forces the sparse-points path rather than silently presenting a dense map as if
it had been filtered.

**Getting to Tier B** needs a capture with TLV 5 enabled and a deliberately
reduced geometry — bandwidth is the constraint, not code. A 128×128 RD map is
32 KB/frame; at 20 fps that is 655 KB/s against a 921600-baud UART's ~92 KB/s
ceiling, 7× over. The `doppler` preset (64×64 at 8 fps) is the config that fits.
See [extraction/cfg/README.md](../extraction/cfg/README.md).

**Angle (TLV 4/8) is a separate axis from the tiers.** When a capture carries it,
`core/angle.py` beamforms the complex per-antenna I/Q host-side and the pipeline
adds an azimuth-time panel. Ghost work needs this: a specular ghost is *defined*
by arriving from the wrong direction, and no tier of range/Doppler data alone can
discriminate one. The `ghost` preset enables it.

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

**Empirical note.** On `run_20260725_092656`, MTI showed **0.00** Jaccard overlap
with `method4_mirror_geometry`. That is the design claim confirmed: a ghost of a
*moving* person is itself moving, so MTI cannot touch it. Any method that appears
to beat MTI should be checked for merely removing more static.

---

## Validation

There is **no labelled ghost ground truth**, so no method here is validated —
they are physically-motivated hypotheses. Two things partially compensate:

- `agreement_matrix()` — where methods disagree is where to look.
- `cadence_from_spectrum()` — an independent cadence estimate. Peak-picking and
  spectral estimation fail differently, so large disagreement means the step
  detector's thresholds are wrong.

### Two open caveats, both load-bearing

**Gait metrics are not yet trustworthy.** On the test run the two independent
cadence estimators disagreed by 21% at spectral confidence 0.02. There is also a
capture-side reason: every recording from 2026-09-26 used `angle-probe`, a 5 fps
diagnostic preset, which gives roughly 5 samples per stride. That is a Nyquist
problem in the data and no analysis code can repair it — those runs need
recapturing with the `gait` preset (25 fps) before their gait numbers mean
anything.

**The controlled experiment that would settle the ghost question has not been
run.** Capture the same scene twice, `multiObjBeamForming -1 1 0.5` and
`-1 0 0.5`. Points present in the first and absent in the second are
device-declared secondary peaks by construction. See
`../../GHOST_VALIDATION_PLAN.txt` for the full programme, including the movable
reflector panel intervention.

---

## Archive

`archive/legacy_parsers/` keeps `dat_parser_plots_v2.py` and friends fully
functional, with `ti_mmw_official_tool/` alongside so their imports resolve:

```bash
cd analysis/archive/legacy_parsers && python dat_parser_plots_v2.py
```

Kept for reference and for reproducing earlier figures. Note that TI's
`parser_mmw_demo.py` treats a zero-detection frame as a fatal error and reads the
int16 SNR as unsigned — costing 34–93% of frames on the through-wall recordings.
See [extraction/README.md §2](../extraction/README.md). Do not build new work on
it.
