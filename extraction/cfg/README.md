# Configurations

Byte-identical copies of `.cfg` files produced by the TI Visualizer's
**SAVE CONFIG TO PC** button. Do not hand-edit these — regenerate them from the
Visualizer so the file always matches what the device was actually given.

## `xwr68xx_AOP_10fps.cfg`

Source: `Downloads/walking_parallel_shorter_length/xwr68xx_AOP_profile_2026_05_06T03_52_25_818.cfg`
SHA-256 `1CF6B2AE6E02592008C5E6FCFFDC305BAF50E43136398A1C6C48B828D11B2AB9` (unmodified copy).

Verified against the recording captured with it
(`xwr68xx_AOP_processed_stream_2026_05_06T03_51_55_724.dat`, 300 frames):
derived range-bin count matches the stream, frame numbers 23303..23602 with no
gaps, and the observed TLV set `[1,7,2,6,9]` matches the `guiMonitor` line.

| | |
|---|---|
| Platform / SDK | xWR68xx_AOP, mmWave SDK 3.6 |
| Scene classifier | `best_range_res`, 60–64 GHz |
| Antennas | 3 Tx x 4 Rx = 12 virtual |
| Frame rate | **10 fps** (`frameCfg ... 100` = 100 ms) |
| Range | 256 bins, 0.0436 m resolution, 8.93 m max |
| Doppler | 16 bins, 0.1217 m/s resolution, ±0.973 m/s max |
| Plots enabled | scatter, range profile, statistics (`guiMonitor -1 1 1 0 0 0 1`) |

### On-chip gating in this config

These run on the radar DSP and drop detections **before** the UART. They are the
only place points actually disappear:

```
cfarCfg -1 0 ... 15 1        CFAR range threshold 15 dB, peak grouping ON
cfarCfg -1 1 ... 15 1        CFAR doppler threshold 15 dB, peak grouping ON
cfarFovCfg -1 0 0 8.92       range gate   0 .. 8.92 m
cfarFovCfg -1 1 -1 1.00      doppler gate -1 .. +1 m/s
aoaFovCfg -1 -90 90 -90 90   angle gate   full FoV
multiObjBeamForming -1 1 0.5 ON  <-- reports secondary angular peaks at >=50%
clutterRemoval -1 0          OFF <-- static returns are kept
```

For the ghost/multipath work, `multiObjBeamForming` is the one to treat as an
experimental variable: it reports secondary angular peaks within the same
range-Doppler bin, which is a direct generator of reported ghost points. Capture
matched pairs with it at `1 0.5` and at `0 0.5` before drawing conclusions.

With `clutterRemoval` off, expect most points to be static: in the reference
recording 1262 of 1634 points (77 %) had exactly zero Doppler.

## Relationship to the 1 fps config

`mmwave_sensing_toolkit/run_2026-04-01_20-20-16/cfg/door_metal_1fps.cfg` is
identical except `frameCfg ... 1000` (1 fps) and a few extra comment lines. It is
**not** interchangeable: the frame period sets replay timing and the assumed
sample interval for any time-series analysis. Pair every `.dat` with the `.cfg`
saved in the same session.
