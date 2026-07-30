"""
dat_parser_plots.py
-------------------
Reusable module for parsing TI mmWave .dat files and generating plots.
Updated to support Azimuth, Elevation, and SNR extraction.
"""

import os
import glob
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.animation import FuncAnimation
from mpl_toolkits.mplot3d import Axes3D

from ti_mmw_official_tool.parser_scripts.parser_mmw_demo import parser_one_mmw_demo_output_packet

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------
FRAME_PERIOD = 0.1

# --------------------------------------------------
# FILE UTILITIES
# --------------------------------------------------

def find_latest_dat_file(directory_path):
    search_pattern = os.path.join(directory_path, '*.dat')
    list_of_files = glob.glob(search_pattern)
    if not list_of_files:
        return None
    return max(list_of_files, key=os.path.getmtime)

# --------------------------------------------------
# PARSER
# --------------------------------------------------

def parse_dat_file(dat_file):
    """
    Parse a TI mmWave .dat binary file into per-frame point clouds,
    velocity, azimuth, elevation, and SNR arrays.
    """
    with open(dat_file, "rb") as fp:
        allBinData = fp.read()

    readNumBytes = len(allBinData)
    totalBytesParsed = 0
    numFramesParsed = 0

    frames_points = []
    frames_velocity = []
    frames_azimuth = []
    frames_elevation = []
    frames_snr = []

    while totalBytesParsed < readNumBytes:
        result = parser_one_mmw_demo_output_packet(
            allBinData[totalBytesParsed:],
            readNumBytes - totalBytesParsed
        )

        if result[0] != 0:
            break

        headerStartIndex = result[1]
        totalPacketNumBytes = result[2]
        numDetObj = result[3]

        totalBytesParsed += headerStartIndex + totalPacketNumBytes
        numFramesParsed += 1

        if numDetObj > 0:
            points = np.column_stack((
                result[6][:numDetObj],   # X
                result[7][:numDetObj],   # Y
                result[8][:numDetObj]    # Z
            ))
            frames_points.append(points)
            frames_velocity.append(np.array(result[9][:numDetObj]))
            frames_azimuth.append(np.array(result[11][:numDetObj]))
            frames_elevation.append(np.array(result[12][:numDetObj]))
            frames_snr.append(np.array(result[13][:numDetObj]))
        else:
            frames_points.append(np.empty((0, 3)))
            frames_velocity.append(np.array([]))
            frames_azimuth.append(np.array([]))
            frames_elevation.append(np.array([]))
            frames_snr.append(np.array([]))

    print("Total frames parsed:", numFramesParsed)
    return frames_points, frames_velocity, frames_azimuth, frames_elevation, frames_snr

# --------------------------------------------------
# DATA PROCESSING
# --------------------------------------------------

def compute_range_frames(frames_points):
    time_axis = []
    range_frames = []
    for i, points in enumerate(frames_points):
        time_axis.append(i * FRAME_PERIOD)
        if len(points) > 0:
            ranges = np.sqrt(np.sum(points**2, axis=1))
            range_frames.append(ranges)
        else:
            range_frames.append(np.array([]))
    return np.array(time_axis), range_frames

def flatten_data(time_axis, frames_points, frames_velocity, frames_azimuth, frames_elevation, frames_snr):
    """
    Flatten per-frame data into parallel 1-D arrays.
    """
    all_times, all_velocities, all_ranges = [], [], []
    all_azimuths, all_elevations, all_snrs = [], [], []

    for i, vels in enumerate(frames_velocity):
        if len(vels) > 0:
            pts = frames_points[i]
            rngs = np.sqrt(np.sum(pts**2, axis=1))
            
            n_pts = len(vels)
            all_times.extend([time_axis[i]] * n_pts)
            all_velocities.extend(vels)
            all_ranges.extend(rngs)
            all_azimuths.extend(frames_azimuth[i])
            all_elevations.extend(frames_elevation[i])
            all_snrs.extend(frames_snr[i])

    return (
        np.array(all_times), np.array(all_velocities), np.array(all_ranges),
        np.array(all_azimuths), np.array(all_elevations), np.array(all_snrs)
    )

# --------------------------------------------------
# PLOTTING UTILITIES
# --------------------------------------------------

def _save_or_show(save_path, ani=None):
    if save_path:
        if ani:
            writer = 'pillow' if save_path.endswith('.gif') else 'ffmpeg'
            ani.save(save_path, writer=writer)
        else:
            plt.savefig(save_path, dpi=300, bbox_inches="tight")
        plt.close()
    else:
        plt.show()

def plot_micro_doppler_high_fidelity(all_times, all_velocities, save_path=None):
    plt.figure(figsize=(12, 5))
    plt.hist2d(all_times, all_velocities, bins=[350, 150], cmap='turbo', 
               norm=mcolors.PowerNorm(gamma=0.4))
    plt.colorbar(label='Reflection Density')
    plt.title("High-Fidelity Micro-Doppler Signature (VT)")
    plt.xlabel("Time (s)")
    plt.ylabel("Velocity (m/s)")
    plt.grid(alpha=0.2)
    _save_or_show(save_path)

def plot_range_time_intensity(all_times, all_ranges, save_path=None):
    plt.figure(figsize=(12, 5))
    plt.hist2d(all_times, all_ranges, bins=[350, 200], cmap='turbo', 
               norm=mcolors.LogNorm())
    plt.colorbar(label='Signal Intensity')
    plt.title("Range-Time Intensity Profile (RT)")
    plt.xlabel("Time (s)")
    plt.ylabel("Range (m)")
    plt.ylim(0, 10)
    _save_or_show(save_path)

def plot_angle_time_intensity(all_times, all_angles, title, ylabel, save_path=None):
    plt.figure(figsize=(12, 5))
    plt.hist2d(all_times, all_angles, bins=[350, 180], cmap='turbo', 
               norm=mcolors.PowerNorm(gamma=0.5))
    plt.colorbar(label='Point Density')
    plt.title(title)
    plt.xlabel("Time (s)")
    plt.ylabel(ylabel)
    plt.grid(alpha=0.2)
    _save_or_show(save_path)

def plot_snr_vs_time_scatter(all_times, all_snrs, save_path=None):
    plt.figure(figsize=(10, 5))
    plt.scatter(all_times, all_snrs, s=5, c=all_snrs, cmap='plasma', alpha=0.6)
    plt.colorbar(label="SNR")
    plt.xlabel("Time (s)")
    plt.ylabel("SNR")
    plt.title("Signal-to-Noise Ratio vs Time")
    plt.grid(True)
    _save_or_show(save_path)

def plot_range_vs_time_scatter(time_axis, range_frames, save_path=None):
    plt.figure()
    for i in range(len(range_frames)):
        if len(range_frames[i]) > 0:
            plt.scatter(np.ones(len(range_frames[i])) * time_axis[i], range_frames[i], s=5)
    plt.xlabel("Time (s)")
    plt.ylabel("Range (m)")
    plt.title("Range vs Time")
    plt.grid()
    _save_or_show(save_path)

def plot_velocity_vs_time_scatter(all_times, all_velocities, save_path=None):
    plt.figure(figsize=(10, 5))
    plt.scatter(all_times, all_velocities, s=5, c=all_velocities, cmap='viridis')
    plt.colorbar(label="Velocity (m/s)")
    plt.xlabel("Time (s)")
    plt.ylabel("Velocity (m/s)")
    plt.title("Velocity vs Time")
    plt.grid(True)
    _save_or_show(save_path)
# --------------------------------------------------
# COMPARISON PLOTS (Stacked Layouts)
# --------------------------------------------------

def plot_compare_velocity_range(all_times, all_velocities, time_axis, range_frames, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    
    # Top: Velocity
    sc = ax1.scatter(all_times, all_velocities, s=5, c=all_velocities, cmap='viridis')
    ax1.set_ylabel("Velocity (m/s)")
    ax1.set_title("Velocity (Top) vs Range (Bottom)")
    ax1.grid(True, alpha=0.3)
    
    # Bottom: Range Scatter
    for i in range(len(range_frames)):
        if len(range_frames[i]) > 0:
            ax2.scatter(np.ones(len(range_frames[i])) * time_axis[i], range_frames[i], s=5, color='tab:blue')
    ax2.set_ylabel("Range (m)")
    ax2.set_xlabel("Time (s)")
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    _save_or_show(save_path)

def plot_compare_snr_range(all_times, all_snrs, time_axis, range_frames, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    
    # Top: SNR
    ax1.scatter(all_times, all_snrs, s=5, c=all_snrs, cmap='plasma')
    ax1.set_ylabel("SNR")
    ax1.set_title("SNR (Top) vs Range (Bottom)")
    ax1.grid(True)

    # Bottom: Range
    for i in range(len(range_frames)):
        if len(range_frames[i]) > 0:
            ax2.scatter(np.ones(len(range_frames[i])) * time_axis[i], range_frames[i], s=5, color='tab:red')
    ax2.set_ylabel("Range (m)")
    ax2.set_xlabel("Time (s)")
    ax2.grid(True)

    plt.tight_layout()
    _save_or_show(save_path)

def plot_compare_micro_doppler_range(all_times, all_velocities, time_axis, range_frames, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    
    # Top: Micro-Doppler Heatmap
    ax1.hist2d(all_times, all_velocities, bins=[350, 150], cmap='turbo', norm=mcolors.PowerNorm(gamma=0.4))
    ax1.set_ylabel("Velocity (m/s)")
    ax1.set_title("Micro-Doppler Intensity (Top) vs Range (Bottom)")

    # Bottom: Range
    for i in range(len(range_frames)):
        if len(range_frames[i]) > 0:
            ax2.scatter(np.ones(len(range_frames[i])) * time_axis[i], range_frames[i], s=5, color='black', alpha=0.5)
    ax2.set_ylabel("Range (m)")
    ax2.set_xlabel("Time (s)")

    plt.tight_layout()
    _save_or_show(save_path)

def plot_compare_azimuth_range(all_times, all_azimuths, time_axis, range_frames, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    
    # Top: Azimuth Heatmap
    ax1.hist2d(all_times, all_azimuths, bins=[350, 180], cmap='turbo', norm=mcolors.PowerNorm(gamma=0.5))
    ax1.set_ylabel("Azimuth (deg)")
    ax1.set_title("Azimuth Density (Top) vs Range (Bottom)")

    # Bottom: Range
    for i in range(len(range_frames)):
        if len(range_frames[i]) > 0:
            ax2.scatter(np.ones(len(range_frames[i])) * time_axis[i], range_frames[i], s=5, color='tab:green')
    ax2.set_ylabel("Range (m)")
    ax2.set_xlabel("Time (s)")

    plt.tight_layout()
    _save_or_show(save_path)

def plot_compare_elevation_range(all_times, all_elevations, time_axis, range_frames, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    
    # Top: Elevation Heatmap
    ax1.hist2d(all_times, all_elevations, bins=[350, 180], cmap='turbo', norm=mcolors.PowerNorm(gamma=0.5))
    ax1.set_ylabel("Elevation (deg)")
    ax1.set_title("Elevation Density (Top) vs Range (Bottom)")

    # Bottom: Range
    for i in range(len(range_frames)):
        if len(range_frames[i]) > 0:
            ax2.scatter(np.ones(len(range_frames[i])) * time_axis[i], range_frames[i], s=5, color='tab:purple')
    ax2.set_ylabel("Range (m)")
    ax2.set_xlabel("Time (s)")

    plt.tight_layout()
    _save_or_show(save_path)

def plot_compare_rti_range(all_times, all_ranges, time_axis, range_frames, save_path=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
    
    # Top: RTI (Heatmap)
    ax1.hist2d(all_times, all_ranges, bins=[350, 200], cmap='turbo', norm=mcolors.LogNorm())
    ax1.set_ylabel("Range Intensity (m)")
    ax1.set_ylim(0, 10)
    ax1.set_title("Range Intensity Heatmap (Top) vs Range Scatter (Bottom)")

    # Bottom: Range (Scatter)
    for i in range(len(range_frames)):
        if len(range_frames[i]) > 0:
            ax2.scatter(np.ones(len(range_frames[i])) * time_axis[i], range_frames[i], s=5, color='tab:orange')
    ax2.set_ylabel("Range Scatter (m)")
    ax2.set_xlabel("Time (s)")
    ax2.set_ylim(0, 10)

    plt.tight_layout()
    _save_or_show(save_path)

# --------------------------------------------------
# UPDATED CONVENIENCE SAVER
# --------------------------------------------------

def save_all_comparison_plots(output_dir, base_name, all_times, all_velocities, all_ranges,
                              all_azimuths, all_elevations, all_snrs,
                              time_axis, range_frames):
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    def _path(idx, desc): return os.path.join(output_dir, f"COMPARE_{idx:02d}_{desc}.png")

    plot_compare_velocity_range(all_times, all_velocities, time_axis, range_frames, save_path=_path(1, "vel_vs_range"))
    plot_compare_snr_range(all_times, all_snrs, time_axis, range_frames, save_path=_path(2, "snr_vs_range"))
    plot_compare_micro_doppler_range(all_times, all_velocities, time_axis, range_frames, save_path=_path(3, "microDoppler_vs_range"))
    plot_compare_azimuth_range(all_times, all_azimuths, time_axis, range_frames, save_path=_path(4, "azimuth_vs_range"))
    plot_compare_elevation_range(all_times, all_elevations, time_axis, range_frames, save_path=_path(5, "elevation_vs_range"))
    plot_compare_rti_range(all_times, all_ranges, time_axis, range_frames, save_path=_path(6, "rti_vs_range"))

# --------------------------------------------------
# CONVENIENCE: SAVE ALL STANDARD PLOTS
# --------------------------------------------------

def save_all_plots(output_dir, base_name,
                   all_times, all_velocities, all_ranges,
                   all_azimuths, all_elevations, all_snrs,
                   time_axis, range_frames, frames_points, frames_velocity):
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    def _path(idx, desc): return os.path.join(output_dir, f"{idx:02d}_{desc}.png")

    plot_range_time_intensity(all_times, all_ranges, save_path=_path(1, "range_time_intensity"))
    plot_micro_doppler_high_fidelity(all_times, all_velocities, save_path=_path(2, "micro_doppler_high_fidelity"))
    plot_range_vs_time_scatter(time_axis, range_frames, save_path=_path(3, "range_vs_time_scatter"))
    plot_velocity_vs_time_scatter(all_times, all_velocities, save_path=_path(4, "velocity_vs_time_scatter"))
    plot_angle_time_intensity(all_times, all_azimuths, "Azimuth vs Time", "Azimuth (deg)", save_path=_path(5, "azimuth_vs_time"))
    plot_angle_time_intensity(all_times, all_elevations, "Elevation vs Time", "Elevation (deg)", save_path=_path(6, "elevation_vs_time"))
    plot_snr_vs_time_scatter(all_times, all_snrs, save_path=_path(7, "snr_vs_time"))
# --------------------------------------------------
# ENTRY POINT
# --------------------------------------------------

if __name__ == "__main__":
    
    # 1. Locate File
    # your_path = r"C:\Users\c1op3\Downloads"
    # dat_file = find_latest_dat_file(your_path)
    dat_file = r"C:\Users\c1op3\Downloads\xwr68xx_AOP_processed_stream_2026_02_26T06_17_54_793.dat"
    print(f"Targeting file: {dat_file}")
    
    # 2. Parse (Updated to catch all 5 arrays)
    frames_points, frames_velocity, frames_azimuth, frames_elevation, frames_snr = parse_dat_file(dat_file)

    # 3. Compute derived data structures
    time_axis, range_frames = compute_range_frames(frames_points)
    
    # Flatten all data (Updated to catch all 6 flattened arrays)
    (all_times, all_velocities, all_ranges, 
     all_azimuths, all_elevations, all_snrs) = flatten_data(
         time_axis, frames_points, frames_velocity, 
         frames_azimuth, frames_elevation, frames_snr
    )

    # ---------------------------
    # Display Plots Interactively
    # ---------------------------
    
    # Original Static Plots
    plot_range_vs_time_scatter(time_axis, range_frames)
    plot_velocity_vs_time_scatter(all_times, all_velocities)
    
    # High-Fidelity Density Maps (Micro-Doppler & RTI)
    plot_micro_doppler_high_fidelity(all_times, all_velocities)
    plot_range_time_intensity(all_times, all_ranges)

    # NEW: Angular & Signal Strength Plots
    plot_angle_time_intensity(all_times, all_azimuths, "Azimuth vs Time", "Azimuth (deg)")
    plot_angle_time_intensity(all_times, all_elevations, "Elevation vs Time", "Elevation (deg)")
    plot_snr_vs_time_scatter(all_times, all_snrs)

    plot_compare_velocity_range(all_times, all_velocities, time_axis, range_frames)
    plot_compare_snr_range(all_times, all_snrs, time_axis, range_frames)
    plot_compare_micro_doppler_range(all_times, all_velocities, time_axis, range_frames)
    plot_compare_azimuth_range(all_times, all_azimuths, time_axis, range_frames)
    plot_compare_elevation_range(all_times, all_elevations, time_axis, range_frames)
    plot_compare_rti_range(all_times, all_ranges, time_axis, range_frames)