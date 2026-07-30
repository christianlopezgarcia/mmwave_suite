"""
dat_parser_plots.py
-------------------
Reusable module for parsing TI mmWave .dat files and generating plots.
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

from ti_mmw_official_tool.parser_scripts.parser_mmw_demo import parser_one_mmw_demo_output_packet

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------
NUM_CHUNKS = 24
MAGIC_WORD = bytes([2, 1, 4, 3, 6, 5, 8, 7])

# --------------------------------------------------
# PARSER UTILITIES & MULTIPROCESSING
# --------------------------------------------------

def find_latest_dat_file(directory_path):
    search_pattern = os.path.join(directory_path, '*.dat')
    list_of_files = glob.glob(search_pattern)
    if not list_of_files:
        return None
    return max(list_of_files, key=os.path.getmtime)

def get_chunk_boundaries(filepath, num_chunks):
    file_size = os.path.getsize(filepath)
    if file_size == 0:
        return []
    chunk_size = file_size // num_chunks
    boundaries = [0]
    with open(filepath, 'rb') as f:
        for i in range(1, num_chunks):
            target_start = i * chunk_size
            f.seek(target_start)
            search_window = f.read(1024 * 1024) 
            idx = search_window.find(MAGIC_WORD)
            if idx != -1:
                boundaries.append(target_start + idx)
            else:
                boundaries.append(target_start) 
    boundaries.append(file_size)
    return [(boundaries[i], boundaries[i+1]) for i in range(len(boundaries)-1) if boundaries[i] != boundaries[i+1]]

def parse_chunk(args):
    filepath, start_idx, end_idx = args
    with open(filepath, 'rb') as f:
        f.seek(start_idx)
        chunk_data = f.read(end_idx - start_idx)
    readNumBytes = len(chunk_data)
    totalBytesParsed = 0
    c_frames_points, c_frames_velocity = [], []
    c_frames_azimuth, c_frames_elevation = [], []
    c_frames_snr, c_frames_cycles = [], []
    while totalBytesParsed < readNumBytes:
        result = parser_one_mmw_demo_output_packet(chunk_data[totalBytesParsed:], readNumBytes - totalBytesParsed)
        if result[0] != 0: break
        headerStartIndex = result[1]
        totalPacketNumBytes = result[2]
        numDetObj = result[3]
        timeCpuCycles = result[-1]
        totalBytesParsed += headerStartIndex + totalPacketNumBytes
        c_frames_cycles.append(timeCpuCycles)
        if numDetObj > 0:
            points = np.column_stack((result[6][:numDetObj], result[7][:numDetObj], result[8][:numDetObj]))
            c_frames_points.append(points)
            c_frames_velocity.append(np.array(result[9][:numDetObj]))
            c_frames_azimuth.append(np.array(result[11][:numDetObj]))
            c_frames_elevation.append(np.array(result[12][:numDetObj]))
            c_frames_snr.append(np.array(result[13][:numDetObj]))
        else:
            c_frames_points.append(np.empty((0, 3)))
            c_frames_velocity.append(np.array([]))
            c_frames_azimuth.append(np.array([]))
            c_frames_elevation.append(np.array([]))
            c_frames_snr.append(np.array([]))
    return c_frames_points, c_frames_velocity, c_frames_azimuth, c_frames_elevation, c_frames_snr, c_frames_cycles

def parse_dat_file_parallel(dat_file, num_chunks=NUM_CHUNKS):
    chunks = get_chunk_boundaries(dat_file, num_chunks)
    args = [(dat_file, start, end) for start, end in chunks]
    print(f"Spawning {len(chunks)} parallel workers...")
    frames_points, frames_velocity = [], []
    frames_azimuth, frames_elevation = [], []
    frames_snr, frames_cycles = [], []
    with ProcessPoolExecutor(max_workers=multiprocessing.cpu_count()) as executor:
        results = list(executor.map(parse_chunk, args))
    for res in results:
        frames_points.extend(res[0]); frames_velocity.extend(res[1])
        frames_azimuth.extend(res[2]); frames_elevation.extend(res[3])
        frames_snr.extend(res[4]); frames_cycles.extend(res[5])
    return frames_points, frames_velocity, frames_azimuth, frames_elevation, frames_snr, frames_cycles

# --------------------------------------------------
# DATA PROCESSING
# --------------------------------------------------

def unwrap_hardware_time(frames_cycles):
    time_axis = []
    rollovers = 0
    prev_cycle = 0
    for cycle in frames_cycles:
        if cycle < prev_cycle and prev_cycle > 2**31:
            rollovers += 1
        prev_cycle = cycle
        absolute_cycles = cycle + (rollovers * (2**32))
        time_axis.append(absolute_cycles / 200e6)
    return np.array(time_axis)

def compute_range_frames(time_axis, frames_points):
    normalized_time = time_axis - time_axis[0] if len(time_axis) > 0 else time_axis
    range_frames = [np.linalg.norm(pts, axis=1) if len(pts) > 0 else np.array([]) for pts in frames_points]
    return normalized_time, range_frames

def flatten_data(time_axis, frames_points, frames_velocity, frames_azimuth, frames_elevation, frames_snr):
    all_times, all_velocities, all_ranges = [], [], []
    all_azimuths, all_elevations, all_snrs = [], [], []
    for i, vels in enumerate(frames_velocity):
        if len(vels) > 0:
            n_pts = len(vels)
            all_times.extend([time_axis[i]] * n_pts)
            all_velocities.extend(vels)
            all_ranges.extend(np.linalg.norm(frames_points[i], axis=1))
            all_azimuths.extend(frames_azimuth[i])
            all_elevations.extend(frames_elevation[i])
            all_snrs.extend(frames_snr[i])
    return (np.array(all_times), np.array(all_velocities), np.array(all_ranges),
            np.array(all_azimuths), np.array(all_elevations), np.array(all_snrs))

# --------------------------------------------------
# PLOTTING UTILITIES
# --------------------------------------------------

def _save_or_show(save_path):
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches="tight")
        plt.close()
    else: plt.show()

def plot_micro_doppler_high_fidelity(all_times, all_velocities, save_path=None):
    plt.figure(figsize=(12, 5))
    plt.hist2d(all_times, all_velocities, bins=[350, 150], cmap='turbo', norm=mcolors.PowerNorm(gamma=0.4))
    plt.colorbar(label='Reflection Density')
    plt.title("High-Fidelity Micro-Doppler Signature (VT)")
    plt.xlabel("Time (s)"); plt.ylabel("Velocity (m/s)"); plt.grid(alpha=0.2)
    _save_or_show(save_path)

def plot_range_time_intensity(all_times, all_ranges, save_path=None):
    plt.figure(figsize=(12, 5))
    plt.hist2d(all_times, all_ranges, bins=[350, 200], cmap='turbo', norm=mcolors.LogNorm())
    plt.colorbar(label='Signal Intensity')
    plt.title("Range-Time Intensity Profile (RT)")
    plt.xlabel("Time (s)"); plt.ylabel("Range (m)"); plt.ylim(0, 10)
    _save_or_show(save_path)

def plot_angle_time_intensity(all_times, all_angles, title, ylabel, save_path=None):
    plt.figure(figsize=(12, 5))
    plt.hist2d(all_times, all_angles, bins=[350, 180], cmap='turbo', norm=mcolors.PowerNorm(gamma=0.5))
    plt.colorbar(label='Point Density')
    plt.title(title); plt.xlabel("Time (s)"); plt.ylabel(ylabel); plt.grid(alpha=0.2)
    _save_or_show(save_path)

def plot_snr_vs_time_scatter(all_times, all_snrs, save_path=None):
    plt.figure(figsize=(10, 5))
    plt.scatter(all_times, all_snrs, s=1, c=all_snrs, cmap='plasma', alpha=0.4, rasterized=True)
    plt.colorbar(label="SNR")
    plt.xlabel("Time (s)"); plt.ylabel("SNR"); plt.title("Signal-to-Noise Ratio vs Time"); plt.grid(True)
    _save_or_show(save_path)

def plot_range_vs_time_scatter(all_times, all_ranges, save_path=None):
    plt.figure(figsize=(10, 5))
    plt.scatter(all_times, all_ranges, s=1, alpha=0.4, rasterized=True)
    plt.xlabel("Time (s)"); plt.ylabel("Range (m)"); plt.title("Range vs Time"); plt.grid()
    _save_or_show(save_path)

def plot_velocity_vs_time_scatter(all_times, all_velocities, save_path=None):
    plt.figure(figsize=(10, 5))
    plt.scatter(all_times, all_velocities, s=1, c=all_velocities, cmap='viridis', alpha=0.4, rasterized=True)
    plt.colorbar(label="Velocity (m/s)")
    plt.xlabel("Time (s)"); plt.ylabel("Velocity (m/s)"); plt.title("Velocity vs Time"); plt.grid(True)
    _save_or_show(save_path)

# --------------------------------------------------
# COMPARISON PLOTS (Stacked Layouts)
# --------------------------------------------------

def plot_compare_velocity_range(all_times, all_velocities, all_ranges, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    ax1.scatter(all_times, all_velocities, s=1, c=all_velocities, cmap='viridis', alpha=0.4, rasterized=True)
    ax1.set_ylabel("Velocity (m/s)"); ax1.set_title("Velocity (Top) vs Range (Bottom)"); ax1.grid(True, alpha=0.3)
    ax2.scatter(all_times, all_ranges, s=1, color='tab:blue', alpha=0.4, rasterized=True)
    ax2.set_ylabel("Range (m)"); ax2.set_xlabel("Time (s)"); ax2.grid(True, alpha=0.3)
    plt.tight_layout()
    _save_or_show(save_path)

def plot_compare_snr_range(all_times, all_snrs, all_ranges, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    ax1.scatter(all_times, all_snrs, s=1, c=all_snrs, cmap='plasma', alpha=0.4, rasterized=True)
    ax1.set_ylabel("SNR"); ax1.set_title("SNR (Top) vs Range (Bottom)"); ax1.grid(True)
    ax2.scatter(all_times, all_ranges, s=1, color='tab:red', alpha=0.4, rasterized=True)
    ax2.set_ylabel("Range (m)"); ax2.set_xlabel("Time (s)"); ax2.grid(True)
    plt.tight_layout()
    _save_or_show(save_path)

def plot_compare_micro_doppler_range(all_times, all_velocities, all_ranges, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    ax1.hist2d(all_times, all_velocities, bins=[350, 150], cmap='turbo', norm=mcolors.PowerNorm(gamma=0.4))
    ax1.set_ylabel("Velocity (m/s)"); ax1.set_title("Micro-Doppler Intensity (Top) vs Range (Bottom)")
    ax2.scatter(all_times, all_ranges, s=1, color='black', alpha=0.3, rasterized=True)
    ax2.set_ylabel("Range (m)"); ax2.set_xlabel("Time (s)")
    plt.tight_layout()
    _save_or_show(save_path)

def plot_compare_azimuth_range(all_times, all_azimuths, all_ranges, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    ax1.hist2d(all_times, all_azimuths, bins=[350, 180], cmap='turbo', norm=mcolors.PowerNorm(gamma=0.5))
    ax1.set_ylabel("Azimuth (deg)"); ax1.set_title("Azimuth Density (Top) vs Range (Bottom)")
    ax2.scatter(all_times, all_ranges, s=1, color='tab:green', alpha=0.4, rasterized=True)
    ax2.set_ylabel("Range (m)"); ax2.set_xlabel("Time (s)")
    plt.tight_layout()
    _save_or_show(save_path)

def plot_compare_elevation_range(all_times, all_elevations, all_ranges, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    ax1.hist2d(all_times, all_elevations, bins=[350, 180], cmap='turbo', norm=mcolors.PowerNorm(gamma=0.5))
    ax1.set_ylabel("Elevation (deg)"); ax1.set_title("Elevation Density (Top) vs Range (Bottom)")
    ax2.scatter(all_times, all_ranges, s=1, color='tab:purple', alpha=0.4, rasterized=True)
    ax2.set_ylabel("Range (m)"); ax2.set_xlabel("Time (s)")
    plt.tight_layout()
    _save_or_show(save_path)

def plot_compare_rti_range(all_times, all_ranges, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    ax1.hist2d(all_times, all_ranges, bins=[350, 200], cmap='turbo', norm=mcolors.LogNorm())
    ax1.set_ylabel("Range Intensity (m)"); ax1.set_ylim(0, 10); ax1.set_title("Heatmap vs Scatter")
    ax2.scatter(all_times, all_ranges, s=1, color='tab:orange', alpha=0.4, rasterized=True)
    ax2.set_ylabel("Range Scatter (m)"); ax2.set_xlabel("Time (s)"); ax2.set_ylim(0, 10)
    plt.tight_layout()
    _save_or_show(save_path)

# --------------------------------------------------
# ENTRY POINT
# --------------------------------------------------

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # dat_file = r"C:\Users\c1op3\Desktop\Occupations\Engineering Career\Ms_EEE\EEE_500\repo\mmwave_sensing_toolkit\run_2026-04-01_20-20-16\raw\run_2026-04-01_20-20-16.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\xwr68xx_AOP_processed_stream_2026_02_26T06_17_54_793.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\xwr68xx_AOP_processed_stream_2026_04_22T00_21_57_344.dat"
    # dat_file = r"C:\Users\c1op3\Desktop\Occupations\Engineering Career\Ms_EEE\EEE_500\repo\mmwave_sensing_toolkit\dat_directory\walking_from_point1_to_point6\xwr68xx_AOP_processed_stream_2026_04_22T00_37_20_944.dat"
    # dat_file = r"C:\Users\c1op3\Desktop\Occupations\Engineering Career\Ms_EEE\EEE_500\repo\mmwave_sensing_toolkit\dat_directory\walking_point_1_to_point5_increment_of_5seconds\xwr68xx_AOP_processed_stream_2026_04_22T00_36_00_893.dat"
    # dat_file = r"C:\Users\c1op3\Desktop\Occupations\Engineering Career\Ms_EEE\EEE_500\repo\mmwave_sensing_toolkit\dat_directory\walking_from_1_to_3\xwr68xx_AOP_processed_stream_2026_04_22T00_33_31_210.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\walking_left_right_cs_2nd_floor\xwr68xx_AOP_processed_stream_2026_04_23T22_59_09_726.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\BLOS_left_right_low\xwr68xx_AOP_processed_stream_2026_04_23T23_03_38_033.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\BLOS_left_right_low\xwr68xx_AOP_processed_stream_2026_04_23T23_03_38_033.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\BLOS_left_right_higher\xwr68xx_AOP_processed_stream_2026_04_23T23_05_23_531.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\wave_hand\xwr68xx_AOP_processed_stream_2026_04_23T23_11_14_590.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\walking_left_right_5th_floor_outside_of_lab_doors\xwr68xx_AOP_processed_stream_2026_04_23T23_29_02_862.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\changed settings\xwr68xx_AOP_processed_stream_2026_04_23T23_33_41_839.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\changed_settings_outside_door_left_right\xwr68xx_AOP_processed_stream_2026_04_23T23_40_36_236.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\changed_settings_outside_door_up_down\xwr68xx_AOP_processed_stream_2026_04_23T23_41_24_453.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\best_range_left_right_up_down\xwr68xx_AOP_processed_stream_2026_04_23T23_47_05_611.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\best_range_left_right_up_down_2ft_wall_lab\xwr68xx_AOP_processed_stream_2026_04_24T00_03_09_361.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\best_range_new_flash_left_right_up_down_lab_outside_lab_door\xwr68xx_AOP_processed_stream_2026_04_24T00_08_08_752.dat"
    # dat_file = r"C:\Users\c1op3\Downloads\test4_plywood_triply\radar_at_4ft_infront_of_wall_professor_1ft_8ft\xwr68xx_AOP_processed_stream_2026_07_15T00_46_55_906.dat"
    dat_file = r"repo\runs\run_20260725_092656\run_20260725_092656.dat"
    start_time = time.time()
    # 1. Parse
    f_points, f_vel, f_az, f_el, f_snr, f_cycles = parse_dat_file_parallel(dat_file)
    print(f"Parsing Time: {time.time() - start_time:.2f} seconds")
    # 2. Process
    time_axis = unwrap_hardware_time(f_cycles)
    norm_time, _ = compute_range_frames(time_axis, f_points)
    
    # 3. Flatten (REQUIRED for large data plotting speed)
    t, v, r, az, el, snr = flatten_data(norm_time, f_points, f_vel, f_az, f_el, f_snr)

    # 4. Setup Dir
    save_dir = os.path.join(os.path.dirname(dat_file), f"Plots_{datetime.now().strftime('%H%M%S')}")
    os.makedirs(save_dir, exist_ok=True)

    # 5. Generate Plots (using your exact functions)
    plot_range_vs_time_scatter(t, r, save_path=os.path.join(save_dir, "range_vs_time.png"))
    plot_velocity_vs_time_scatter(t, v, save_path=os.path.join(save_dir, "velocity_vs_time.png"))
    plot_micro_doppler_high_fidelity(t, v, save_path=os.path.join(save_dir, "micro_doppler.png"))
    plot_range_time_intensity(t, r, save_path=os.path.join(save_dir, "rti.png"))
    
    # Comparison Plots
    plot_compare_velocity_range(t, v, r, save_path=os.path.join(save_dir, "comp_vel_range.png"))
    plot_compare_snr_range(t, snr, r, save_path=os.path.join(save_dir, "comp_snr_range.png"))
    plot_compare_micro_doppler_range(t, v, r, save_path=os.path.join(save_dir, "comp_md_range.png"))
    plot_compare_azimuth_range(t, az, r, save_path=os.path.join(save_dir, "comp_az_range.png"))
    plot_compare_elevation_range(t, el, r, save_path=os.path.join(save_dir, "comp_el_range.png"))
    plot_compare_rti_range(t, r, save_path=os.path.join(save_dir, "comp_rti_range.png"))

    print(f"Success. Plots saved to {save_dir}")