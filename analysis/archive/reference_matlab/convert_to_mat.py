# -*- coding: utf-8 -*-
"""
Convert radar.npy -> radar_recording.mat
Raw shape: (F=576, R=3, C=128, S=280) uint16
MATLAB expects adc: (F, C, R, S) = (frames, chirps, rx, samples)
Transpose: (F, R, C, S) -> (F, C, R, S)
ADC offset: subtract 2048
"""

import numpy as np
import scipy.io as sio
import json, os

def convert(npy_path, config_path, out_path):
    print(f"\nLoading: {npy_path}")
    if not os.path.isfile(npy_path):
        print(f"  ERROR: file not found!")
        return

    raw = np.load(npy_path)
    print(f"  Raw shape : {raw.shape}  dtype={raw.dtype}")

    # raw = (F, R, C, S) → transpose to (F, C, R, S)
    F, R, C, S = raw.shape
    adc = raw.transpose(0, 2, 1, 3).astype(np.float32)
    adc = adc - 2048.0
    print(f"  Output adc: (F={F}, C={C}, R={R}, S={S})")
    print(f"  Value range: [{adc.min():.0f}, {adc.max():.0f}]")

    config_str = '{}'
    if os.path.isfile(config_path):
        with open(config_path, 'r') as f:
            config_str = f.read()
        print(f"  Config: OK")

    sio.savemat(out_path, {'adc': adc, 'config': config_str})
    print(f"  Saved -> {out_path}")

WALK = (r"C:\Users\yoginathganditi\OneDrive - California State University, "
        r"Sacramento\Documents\RadarFusionGUI\BGT60TR13C"
        r"\two_people_chris20260317-160005\RadarIfxAvian_00")

EMPTY = (r"C:\Users\yoginathganditi\OneDrive - California State University, "
         r"Sacramento\Documents\RadarFusionGUI\BGT60TR13C"
         r"\two persons empty\RadarIfxAvian_00")

convert(os.path.join(WALK,  "radar.npy"),
        os.path.join(WALK,  "config.json"),
        os.path.join(WALK,  "radar_recording.mat"))

convert(os.path.join(EMPTY, "radar.npy"),
        os.path.join(EMPTY, "config.json"),
        os.path.join(EMPTY, "radar_recording.mat"))

print("\nDone. Now run in MATLAB:  out = run_fixed_two_person();")