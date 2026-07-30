"""
dat_parser_plots_v2.py
-------------------
Separates two-person velocity profiles using a Centroid-Based Tracker
derived from the run_fixed_two_person.m logic.
"""

import os
import glob
import time
import struct
import numpy as np
from datetime import datetime
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from sklearn.cluster import KMeans

from ti_mmw_official_tool.parser_scripts.parser_mmw_demo import parser_one_mmw_demo_output_packet

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------
NUM_CHUNKS = 24
MAGIC_WORD = bytes([2, 1, 4, 3, 6, 5, 8, 7])

# TRACKING PARAMETERS (Ported from .m script)
MAX_JUMP_DISTANCE = 0.75  # Equivalent to opt.max_jump_m
MIN_PERSON_SEP = 0.50     # Equivalent to opt.min_person_sep_m
MAX_HOLD_FRAMES = 10      # Equivalent to MAX_HOLD in .m

# --------------------------------------------------
# PARSER UTILITIES
# --------------------------------------------------

def find_latest_dat_file(directory_path):
    search_pattern = os.path.join(directory_path, '*.dat')
    list_of_files = glob.glob(search_pattern)
    return max(list_of_files, key=os.path.getmtime) if list_of_files else None

def get_chunk_boundaries(filepath, num_chunks):
    file_size = os.path.getsize(filepath)
    if file_size == 0: return []
    chunk_size = file_size // num_chunks
    boundaries = [0]
    with open(filepath, 'rb') as f:
        for i in range(1, num_chunks):
            f.seek(i * chunk_size)
            search_window = f.read(1024 * 1024) 
            idx = search_window.find(MAGIC_WORD)
            boundaries.append((i * chunk_size + idx) if idx != -1 else (i * chunk_size))
    boundaries.append(file_size)
    return [(boundaries[i], boundaries[i+1]) for i in range(len(boundaries)-1)]

def parse_chunk(args):
    filepath, start_idx, end_idx = args
    with open(filepath, 'rb') as f:
        f.seek(start_idx)
        chunk_data = f.read(end_idx - start_idx)
    readNumBytes = len(chunk_data)
    totalBytesParsed = 0
    c_frames_points, c_frames_velocity, c_frames_cycles = [], [], []
    while totalBytesParsed < readNumBytes:
        result = parser_one_mmw_demo_output_packet(chunk_data[totalBytesParsed:], readNumBytes - totalBytesParsed)
        if result[0] != 0: break
        c_frames_cycles.append(result[-1])
        numDetObj = result[3]
        if numDetObj > 0:
            points = np.column_stack((result[6][:numDetObj], result[7][:numDetObj], result[8][:numDetObj]))
            c_frames_points.append(points)
            c_frames_velocity.append(np.array(result[9][:numDetObj]))
        else:
            c_frames_points.append(np.empty((0, 3)))
            c_frames_velocity.append(np.array([]))
        totalBytesParsed += result[1] + result[2]
    return c_frames_points, c_frames_velocity, c_frames_cycles

def parse_dat_file_parallel(dat_file):
    chunks = get_chunk_boundaries(dat_file, NUM_CHUNKS)
    args = [(dat_file, s, e) for s, e in chunks]
    with ProcessPoolExecutor(max_workers=multiprocessing.cpu_count()) as executor:
        results = list(executor.map(parse_chunk, args))
    f_p, f_v, f_c = [], [], []
    for res in results:
        f_p.extend(res[0]); f_v.extend(res[1]); f_c.extend(res[2])
    return f_p, f_v, f_c

# --------------------------------------------------
# CORE TRACKING LOGIC (The .m Implementation)
# --------------------------------------------------

def separate_two_persons(time_axis, frames_points, frames_velocity):
    """
    Implements a stateful tracker to maintain identities and separate profiles.
    """
    p1_t, p1_v, p1_r = [], [], []
    p2_t, p2_v, p2_r = [], [], []
    
    # Tracker State
    center1, center2 = None, None
    hold1, hold2 = 0, 0
    
    for i, points in enumerate(frames_points):
        if len(points) == 0: continue
        
        vels = frames_velocity[i]
        ranges = np.linalg.norm(points, axis=1)
        
        # 1. Initialization (Find two starting people)
        if center1 is None or center2 is None:
            if len(points) >= 2:
                km = KMeans(n_clusters=2, n_init=1).fit(points)
                cands = km.cluster_centers_
                if np.linalg.norm(cands[0] - cands[1]) > MIN_PERSON_SEP:
                    center1, center2 = cands[0], cands[1]
            continue

        # 2. Assignment logic (Mimics MATLAB 'cost' matrix)
        # Calculate distance of every point in frame to both tracked centers
        dist_to_c1 = np.linalg.norm(points - center1, axis=1)
        dist_to_c2 = np.linalg.norm(points - center2, axis=1)
        
        # Point belongs to P1 if closer to center1 AND within jump distance
        mask_p1 = (dist_to_c1 < dist_to_c2) & (dist_to_c1 < MAX_JUMP_DISTANCE)
        mask_p2 = (dist_to_c2 <= dist_to_c1) & (dist_to_c2 < MAX_JUMP_DISTANCE)
        
        # 3. Store results for Person 1
        if np.any(mask_p1):
            p1_t.extend([time_axis[i]] * np.sum(mask_p1))
            p1_v.extend(vels[mask_p1]); p1_r.extend(ranges[mask_p1])
            center1 = np.mean(points[mask_p1], axis=0) # Update center (Dynamic Gating)
            hold1 = 0
        else:
            hold1 += 1 # Person lost for this frame (MAX_HOLD logic)

        # 4. Store results for Person 2
        if np.any(mask_p2):
            p2_t.extend([time_axis[i]] * np.sum(mask_p2))
            p2_v.extend(vels[mask_p2]); p2_r.extend(ranges[mask_p2])
            center2 = np.mean(points[mask_p2], axis=0)
            hold2 = 0
        else:
            hold2 += 1
            
        # Reset if tracks are lost for too long
        if hold1 > MAX_HOLD_FRAMES: center1 = None
        if hold2 > MAX_HOLD_FRAMES: center2 = None
                
    return (np.array(p1_t), np.array(p1_v), np.array(p1_r),
            np.array(p2_t), np.array(p2_v), np.array(p2_r))

# --------------------------------------------------
# PLOTTING & MAIN
# --------------------------------------------------

def plot_micro_doppler(t, v, title, path):
    if len(t) == 0: return
    plt.figure(figsize=(12, 5))
    plt.hist2d(t, v, bins=[300, 120], cmap='turbo', norm=mcolors.PowerNorm(gamma=0.5))
    plt.title(title); plt.xlabel("Time (s)"); plt.ylabel("Velocity (m/s)"); plt.grid(alpha=0.3)
    plt.savefig(path, dpi=200); plt.close()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    dat_file = r"C:\Users\c1op3\Desktop\Occupations\Engineering Career\Ms_EEE\EEE_500\repo\mmwave_sensing_toolkit\dat_directory\20260316_215011_fast_slow_speed_walk_up_and_down_living_room_2ft_apart_take1\xwr68xx_AOP_processed_stream_2026_03_17T04_50_00_692.dat"

    
    # 1. Parse & Time Alignment
    f_p, f_v, f_c = parse_dat_file_parallel(dat_file)
    time_raw = np.array(f_c) / 200e6
    norm_time = time_raw - time_raw[0] if len(time_raw) > 0 else time_raw
    
    # 2. Apply Tracker Logic (.m derived)
    (t1, v1, r1, t2, v2, r2) = separate_two_persons(norm_time, f_p, f_v)

    # 3. Save Output
    out_dir = os.path.join(os.path.dirname(dat_file), f"TwoPerson_Output_{datetime.now().strftime('%H%M%S')}")
    os.makedirs(out_dir, exist_ok=True)
    
    plot_micro_doppler(t1, v1, "Velocity Profile - Person 1", os.path.join(out_dir, "VT_Person1.png"))
    plot_micro_doppler(t2, v2, "Velocity Profile - Person 2", os.path.join(out_dir, "VT_Person2.png"))
    
    print(f"Tracking complete. Profiles saved to {out_dir}")