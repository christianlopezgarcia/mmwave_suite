import os
import shutil
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from scipy.spatial import KDTree
from datetime import datetime

from dat_parser_plots import (
    parse_dat_file,
    compute_range_frames,
    find_latest_dat_file,
    flatten_data,
    save_all_plots,
    save_all_comparison_plots,
)

# --------------------------------------------------
# 1. SETTINGS
# --------------------------------------------------
BACKGROUND_PATH = r"repo\mmwave_sensing_toolkit\dat_directory\20260316_213833_Do_Nothing_Large_Living_Room"
SIGNAL_PATH     = r"C:\Users\c1op3\Desktop\Occupations\Engineering Career\Ms_EEE\EEE_500\repo\mmwave_sensing_toolkit\dat_directory\20260316_215140_fast_slow_speed_walk_up_and_down_living_room_2ft_apart_take2"
EXPERIMENT_NAME = "ROI_LargeLivingRoom_FastAndSlowPace_WalkUpAndDown_TwoSubjects_TwoFeetApartRoughly"

# --- BACKGROUND SUBTRACTION SETTINGS ---
THRESHOLD_M = 0.25

# --- FILTER & ROI SETTINGS ---
ROI_MIN_M = 0.5       # Minimum range in meters (ignore near-field clutter)
ROI_MAX_M = 8.0       # Maximum range in meters (gate out distant walls)
MIN_SNR = 10.0        # Reject weak points below this SNR value
MIN_DOPPLER = 0.15    # Reject static/low-energy velocities (m/s) to clean up 0-doppler bins
MAX_DOMINANT_PTS = 5  # Number of dominant (peak SNR) points to keep per frame

# --------------------------------------------------
# 2. PLOTTING UTILITIES (Matrix Helpers)
# --------------------------------------------------

def plot_rti_ax(ax, time_axis, range_frames, title):
    for i in range(len(range_frames)):
        if len(range_frames[i]) > 0:
            ax.scatter(np.ones(len(range_frames[i])) * time_axis[i], range_frames[i], s=5)
    ax.set_title(title)
    ax.set_ylabel("Range (m)")
    ax.set_ylim(0, 10)
    ax.grid(True)

def plot_md_ax(ax, t, v, title):
    if len(t) > 0:
        ax.hist2d(t, v, bins=[350,150], cmap="turbo", norm=mcolors.PowerNorm(gamma=0.4))
    ax.set_title(title)
    ax.set_ylabel("Velocity (m/s)")

def plot_scatter_ax(ax, time_axis, range_frames, title, color):
    for i, rngs in enumerate(range_frames):
        if len(rngs) > 0:
            ax.scatter([time_axis[i]] * len(rngs), rngs, s=3, color=color, alpha=0.3)
    ax.set_title(title)
    ax.set_ylim(0,10)

# --------------------------------------------------
# 3. DATA PIPELINE
# --------------------------------------------------

def run_comparison():
    # Detect files
    bg_file = BACKGROUND_PATH if BACKGROUND_PATH.endswith(".dat") else find_latest_dat_file(BACKGROUND_PATH)
    sig_file = SIGNAL_PATH if SIGNAL_PATH.endswith(".dat") else find_latest_dat_file(SIGNAL_PATH)
    
    # Setup Output Directory
    base_dir = os.path.dirname(os.path.dirname(BACKGROUND_PATH))
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = os.path.join(base_dir, "dat_compare", f"{timestamp}_{EXPERIMENT_NAME}")
    os.makedirs(output_dir, exist_ok=True)

    # Create subfolder for source data and copy folders/files
    source_copy_dir = os.path.join(output_dir, "source_data")
    os.makedirs(source_copy_dir, exist_ok=True)
    
    print(f"Processing Experiment: {EXPERIMENT_NAME}")

    def archive_source(path, label):
        dest = os.path.join(source_copy_dir, label)
        if os.path.isdir(path):
            shutil.copytree(path, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(path, dest)

    archive_source(BACKGROUND_PATH, "background_input")
    archive_source(SIGNAL_PATH, "signal_input")

    # Parse data (5 return values)
    bg_frames, bg_vels, bg_az, bg_el, bg_snr = parse_dat_file(bg_file)
    sig_frames, sig_vels, sig_az, sig_el, sig_snr = parse_dat_file(sig_file)

    # --------------------------------------------------
    # BACKGROUND PRE-FILTERING & MODEL BUILDING
    # --------------------------------------------------
    filtered_bg_frames = []
    for i, frame in enumerate(bg_frames):
        if frame.size > 0:
            ranges = np.linalg.norm(frame, axis=1)
            # Apply ROI and SNR to background to build a cleaner KDTree
            valid_bg = (ranges >= ROI_MIN_M) & (ranges <= ROI_MAX_M) & (bg_snr[i] >= MIN_SNR)
            if np.any(valid_bg):
                filtered_bg_frames.append(frame[valid_bg])

    all_bg_coords = np.vstack(filtered_bg_frames) if filtered_bg_frames else np.empty((0,3))
    bg_tree = KDTree(all_bg_coords) if all_bg_coords.size > 0 else None

    # --------------------------------------------------
    # SIGNAL FILTERING & BACKGROUND SUBTRACTION
    # --------------------------------------------------
    cln_f, cln_v, cln_az, cln_el, cln_snr = [], [], [], [], []

    for i, frame in enumerate(sig_frames):
        # Default empty appends for frames with no valid points
        def append_empty():
            cln_f.append(np.empty((0,3))); cln_v.append(np.array([])); cln_az.append(np.array([]))
            cln_el.append(np.array([])); cln_snr.append(np.array([]))

        if frame.size == 0:
            append_empty()
            continue

        # 1. Calculate Ranges
        ranges = np.linalg.norm(frame, axis=1)

        # 2. Build Masks (ROI, SNR, Doppler)
        roi_mask = (ranges >= ROI_MIN_M) & (ranges <= ROI_MAX_M)
        snr_mask = sig_snr[i] >= MIN_SNR
        doppler_mask = np.abs(sig_vels[i]) >= MIN_DOPPLER
        
        valid_mask = roi_mask & snr_mask & doppler_mask

        valid_frame = frame[valid_mask]
        valid_vels = sig_vels[i][valid_mask]
        valid_az = sig_az[i][valid_mask]
        valid_el = sig_el[i][valid_mask]
        valid_snrs = sig_snr[i][valid_mask]

        if valid_frame.size == 0:
            append_empty()
            continue

        # 3. KDTree Background Subtraction
        if bg_tree is not None:
            dist, _ = bg_tree.query(valid_frame)
            bg_mask = dist > THRESHOLD_M
            
            valid_frame = valid_frame[bg_mask]
            valid_vels = valid_vels[bg_mask]
            valid_az = valid_az[bg_mask]
            valid_el = valid_el[bg_mask]
            valid_snrs = valid_snrs[bg_mask]

        if valid_frame.size == 0:
            append_empty()
            continue

        # 4. Extract Dominant Points by SNR
        dominant_indices = np.argsort(valid_snrs)[::-1] # Sort descending
        top_n = min(MAX_DOMINANT_PTS, len(dominant_indices))
        best_idx = dominant_indices[:top_n]

        cln_f.append(valid_frame[best_idx])
        cln_v.append(valid_vels[best_idx])
        cln_az.append(valid_az[best_idx])
        cln_el.append(valid_el[best_idx])
        cln_snr.append(valid_snrs[best_idx])

    # --------------------------------------------------
    # COMPUTE RANGES AND FLATTEN
    # --------------------------------------------------
    t_sig, r_sig_f = compute_range_frames(sig_frames)
    t_bg , r_bg_f  = compute_range_frames(bg_frames)
    t_cln, r_cln_f = compute_range_frames(cln_f)

    # Flattened data (All parallel arrays)
    sig_t, sig_v, sig_r, sig_az_f, sig_el_f, sig_snr_f = flatten_data(t_sig, sig_frames, sig_vels, sig_az, sig_el, sig_snr)
    bg_t , bg_v , bg_r , bg_az_f , bg_el_f , bg_snr_f  = flatten_data(t_bg , bg_frames , bg_vels , bg_az, bg_el, bg_snr)
    cln_t, cln_v, cln_r, cln_az_f, cln_el_f, cln_snr_f = flatten_data(t_cln, cln_f, cln_v, cln_az, cln_el, cln_snr)

    # --------------------------------------------------
    # PLOTTING
    # --------------------------------------------------
    
    # GRAPH A : Comparison Matrix
    fig, axes = plt.subplots(3, 3, figsize=(20, 16), sharex="col")
    labels = ["1. ORIGINAL SIGNAL", "2. BACKGROUND BASELINE", "3. FILTERED & SUBTRACTED"]
    datasets = [(t_sig, r_sig_f, sig_t, sig_v, 'black'), (t_bg, r_bg_f, bg_t, bg_v, 'red'), (t_cln, r_cln_f, cln_t, cln_v, 'green')]

    for i, (ta, rf, t, v, col) in enumerate(datasets):
        plot_rti_ax(axes[i,0], ta, rf, f"{labels[i]} (Range vs Time)")
        plot_md_ax(axes[i,1], t, v, f"{labels[i]} (Micro-Doppler)")
        plot_scatter_ax(axes[i,2], ta, rf, f"{labels[i]} (Scatter)", col)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "comparison_matrix.png"), dpi=300)
    plt.close()

    # GRAPH B : Final Stacked Analysis
    fig2, (ax_r, ax_v) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
    plot_rti_ax(ax_r, t_cln, r_cln_f, "Cleaned: Range vs Time")
    plot_md_ax(ax_v, cln_t, cln_v, "Cleaned: Micro-Doppler")
    ax_v.set_xlabel("Time (s)")
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "cleaned_summary.png"), dpi=300)
    plt.close()

    # GRAPH C : Save all standard plots
    save_all_plots(
        output_dir = output_dir,
        base_name = "subtracted_final",
        all_times = cln_t, all_velocities = cln_v, all_ranges = cln_r,
        all_azimuths = cln_az_f, all_elevations = cln_el_f, all_snrs = cln_snr_f,
        time_axis = t_cln, range_frames = r_cln_f, frames_points = cln_f, frames_velocity = cln_v
    )
    save_all_comparison_plots(
        output_dir = output_dir,
        base_name = "subtracted_final",
        all_times = cln_t, all_velocities = cln_v, all_ranges = cln_r,
        all_azimuths = cln_az_f, all_elevations = cln_el_f, all_snrs = cln_snr_f,
        time_axis = t_cln, range_frames = r_cln_f
    )

    print(f"Comparison complete. Results and raw data archived in: {output_dir}")

if __name__ == "__main__":
    run_comparison()