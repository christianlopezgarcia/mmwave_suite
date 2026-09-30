# TI reference material — verbatim

Copied unmodified from

```
radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/
    Gesture_Recognition/
```

and, for the host-side class,

```
radar_toolbox_4_00_00_05/tools/visualizers/Applications_Visualizer/
    common/Demo_Classes/gesture_recognition.py
```

Nothing in this directory has been edited. It is here so that every claim in
`../ALGORITHM.md` and `../README.md` can be checked against the source without
a second install, and so the line references stay valid if the toolbox version
on this machine changes. If you upgrade the toolbox, re-copy and re-run
`../tools/export_ti_weights.py`.

## Contents

| path | what it is |
|---|---|
| `src/6443/mss/` | the application: `gesture.c` (features), `main.c` (TLV output, frame loop), `cli.c` (the compiled-in chirp config), `mmw_cli.c` (which CLI commands exist), `mmw_output.h` (TLV codes 1050/1051) |
| `src/6443/include/neuralnet/` | the trained model as C headers — `mean`, `std`, `w0..w2`, `b0..b2`. Source of `../model/ti_ann_6843.npz`. |
| `src/6443/include/dopplerproc/` | **modified** Doppler DPU: linear-magnitude detection matrix, TX1-RX1 only, plus the two angle-FFT paramsets. Diff it against `C:\ti\mmwave_sdk_03_06_02_00-LTS\packages\ti\datapath\dpc\dpu\dopplerproc\src\dopplerprochwa.c` to see what changed. |
| `src/6443/include/objdethwa/` | **modified** object-detection DPC: CFAR and AoA compiled out, feature vector assembly and ANN inference added |
| `src/6443/include/rangeproc/`, `include/utils/` | range DPU and the ANN forward pass |
| `prebuilt_binaries/` | `gesture_ML_6443_AOP.bin` and `..._ODS.bin`, 418 KB each. **AOP is the one for this hardware.** Flash with UniFlash. |
| `docs/` | TI's users guide and release notes. The users guide embeds a video of each of the nine gestures — worth watching before recording training data, since the model is sensitive to how the gesture is performed. |
| `xWRLx432_chirp_configs/` | TI's *other* gesture demo, the "Fixed Distance (2 m)" one for the xWRL1432/6432. Different silicon, so not runnable here, but the configs are the evidence for the range argument in `../README.md`: same bandwidth, same frame rate, more RX aperture, and **two fewer gesture classes**. |
| `visualizer/gesture_recognition.py` | TI's host-side demo class. Independent re-implementation of the debounce from TLV 1051, and the source of the alternative probability thresholds in `../postproc.py`. |

## Flashing

`docs/Gesture_with_Machine_Learning_users_guide.html` has the full procedure.
Short version for the IWR6843AOPEVM:

1. Fit the SOP2 jumper (flashing mode), power-cycle.
2. UniFlash → IWR6843AOP → the **CLI/Enhanced** COM port → load
   `prebuilt_binaries/gesture_ML_6443_AOP.bin` as the *Meta Image 1* → Load Images.
3. Remove SOP2, power-cycle. The demo starts chirping on its own — there is no
   config to send.

To go back, flash your out-of-box binary the same way. Nothing here is
one-way.

TI's note, worth repeating: the binary is built for the xWR6443 but runs on the
xWR6843, because the demo uses only the HWA and never the C674x DSP that the
6443 lacks. Not verified on this specific module.

## Licence

TI's source carries the "Limited License" header reproduced in each file:
redistribution permitted, **for use only with TI devices**, no reverse
engineering of the binaries. The prebuilt `.bin` files are redistributable
under the same terms. This directory is a verbatim copy, so those terms travel
with it.

Note that this constrains the repository as a whole if it is ever published —
see the open LICENSE question for the suite. The derived work in the parent
directory (`../ann.py`, `../features.py`, and the rest) is a clean-room-free
port: it was written by reading TI's C, so it is a derivative of it and
inherits the same TI-devices-only restriction. The extracted weights in
`../model/` are likewise TI's.
