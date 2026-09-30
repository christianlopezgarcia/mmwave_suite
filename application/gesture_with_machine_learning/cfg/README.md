# Gesture configs

Three files, for three different situations. Unlike `extraction/cfg/*.cfg`
these are **hand-written**, not emitted by
`extraction.highfidelity.configs` — the gesture geometry sits outside what
that generator's presets cover (single-TX, sub-metre, 30 fps). Each file's
header block states its own derived numbers; `../tools/range_budget.py`
recomputes them all from scratch, so the two can be cross-checked.

| file | firmware | sent? | fps | dR | Ndopp | dV | payload |
|---|---|---|---|---|---|---|---|
| `xwr68xx_AOP_gesture_ti-onchip_28fps.cfg` | TI gesture | **no** | 28.6 | 5.35 cm | 128 | 0.095 | TLV 1050+1051, 128 B |
| `xwr68xx_AOP_gesture_points_30fps.cfg` | out-of-box | yes | 30.0 | 5.86 cm | 128 | 0.129 | point cloud, ~1.4 kB |
| `xwr68xx_AOP_gesture_rdmap_8fps.cfg` | out-of-box | yes | 8.0 | 5.86 cm | 128 | 0.115 | + heat map, 9.1 kB |

## `..._ti-onchip_28fps.cfg` — documentation, not a command list

A verbatim transcription of the config TI's gesture firmware replays to itself
at boot (`cli.c:85-115`). **It is never sent.** That firmware registers no
`profileCfg`/`frameCfg`/`chirpCfg`, so every line would return an error, and
the leading `sensorStop` would halt a sensor whose chirp you then cannot
reconfigure. `common.app.run_live(send_config=False)` handles this, and the
CLI defaults to it in the `onchip` modes.

It exists so `parse_cfg` can derive the TLV geometry for the parser, so the
run directory records what the board was actually doing, and so the config is
readable without opening C.

## `..._points_30fps.cfg` — the live out-of-box option

The one to use for `--mode points` and for recording training takes.

Three deliberate departures from TI:

- **3 TX instead of 1.** TDM-MIMO gives a 4-element azimuth aperture instead
  of 2, which halves the residual-phase angle error floor. That floor is what
  actually sets swipe range (1.1 m → 3.2 m geometric ceiling), and it is the
  only term a bigger aperture improves. Cost: 3× the chirp repeat time, paid
  for with a short 66 µs chirp.
- **`clutterRemoval` and `calibDcRangeSig` both ON.** TI leaves both off
  because their firmware suppresses zero Doppler in the detection matrix
  instead. Here CFAR does the detecting, and without these the TX-RX antenna
  coupling in range bins 0-2 is a permanent false target sitting in the
  gesture zone.
- **CFAR peak grouping OFF** on both axes. Grouping collapses the hand to one
  point; the features want its spatial spread. `HostFeatureConfig.max_points`
  does the count-limiting instead, which also restores the constant-weight
  property TI gets from its fixed-count top-10 loop.

`cfarFovCfg` gates detections to 0.05–1.00 m. If you widen it, widen
`HostFeatureConfig.range_max_m` to match, or the host gate silently discards
what you just paid UART bandwidth for.

## `..._rdmap_8fps.cfg` — offline instrument

TLV 5 at 32×128 costs 8192 B every frame, whole; there is no out-of-box
command to send a subset, and the gesture features only ever read 7 range bins
of 64. At 921600 baud that is a 10.1 fps ceiling, and a gesture needs 25-30.

This config already spends the budget as well as it can — 32 range bins rather
than 64 (max range 1.87 m, resolution kept at 5.9 cm by halving the *sample
rate* rather than the bandwidth) and 1 TX to match TI's 4-virtual-antenna
geometry exactly. 8 fps is the result.

**Before accepting that:** run `../tools/uart_probe.py`. The firmware
advertises up to 3.125 Mbaud on the data port, at which this config would run
at 34 fps and the TLV-5 route would become live-capable. Nobody has measured
whether this EVM's USB bridge sustains it.

## Shared caveat: the IF high-pass corner

All three use `hpfCornerFreq1 = 0` = 175 kHz, which at ~100 MHz/µs is a corner
at **~26 cm** — inside the working range. It attenuates rather than removes
(first order, ~8 dB down at 10 cm), and with 85 dB of SNR on a hand it does not
prevent detection, but it does bias `weightedRange` outward. TI has the same
thing and the trained model has it baked in, so do not "fix" it in the
on-chip config: you would move the features off the model's manifold.
