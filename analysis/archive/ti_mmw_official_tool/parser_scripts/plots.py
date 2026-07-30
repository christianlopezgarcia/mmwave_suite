import os
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from mpl_toolkits.mplot3d import Axes3D

from parser_mmw_demo import parser_one_mmw_demo_output_packet

###############################################################################
# CONFIG
###############################################################################
# capturedFileName = r"C:\Users\c1op3\Downloads\xwr68xx_AOP_processed_stream_2026_02_18T23_40_02_499.dat"

import glob
import os

def find_latest_dat_file(directory_path):
    # Construct the search pattern for .dat files in the specified directory
    search_pattern = os.path.join(directory_path, '*.dat')
    
    # Get a list of all files matching the pattern
    list_of_files = glob.glob(search_pattern)
    
    # Check if any files were found
    if not list_of_files:
        return None # No .dat files found
        
    # Sort the files by their last modification time (os.path.getmtime)
    # The 'key' argument uses a lambda function to get the modification time for each file
    # 'reverse=True' sorts in descending order, putting the latest file first
    latest_file = max(list_of_files, key=os.path.getmtime)
    
    return latest_file


# capturedFileName = r"C:\Users\c1op3\Downloads\xwr68xx_AOP_processed_stream_2026_02_18T23_40_02_499.dat"
your_path = r"C:\Users\c1op3\Downloads"
# If the files are in the current working directory, you can use '.'
# your_path = '.' 

capturedFileName = find_latest_dat_file(your_path)

if capturedFileName:
    print(f"The latest .dat file found is: {capturedFileName}")
else:
    print(f"No .dat files found in the directory: {your_path}")
###############################################################################
# READ FILE
###############################################################################
fp = open(capturedFileName,'rb')
readNumBytes = os.path.getsize(capturedFileName)
print("readNumBytes:", readNumBytes)
allBinData = fp.read()
fp.close()

###############################################################################
# PARSE ALL FRAMES INTO MEMORY
###############################################################################
totalBytesParsed = 0
numFramesParsed = 0

frames_points = []   # list of Nx3 arrays
frames_velocity = [] # list of N arrays

while totalBytesParsed < readNumBytes:

    parser_result, \
    headerStartIndex, \
    totalPacketNumBytes, \
    numDetObj, \
    numTlv, \
    subFrameNumber, \
    detectedX_array, \
    detectedY_array, \
    detectedZ_array, \
    detectedV_array, \
    detectedRange_array, \
    detectedAzimuth_array, \
    detectedElevation_array, \
    detectedSNR_array, \
    detectedNoise_array = parser_one_mmw_demo_output_packet(
        allBinData[totalBytesParsed:], 
        readNumBytes-totalBytesParsed)

    if parser_result != 0:
        break

    totalBytesParsed += (headerStartIndex + totalPacketNumBytes)
    numFramesParsed += 1

    if numDetObj > 0:
        points = np.column_stack((
            detectedX_array[:numDetObj],
            detectedY_array[:numDetObj],
            detectedZ_array[:numDetObj]
        ))
        velocities = np.array(detectedV_array[:numDetObj])

        frames_points.append(points)
        frames_velocity.append(velocities)
    else:
        frames_points.append(np.empty((0,3)))
        frames_velocity.append(np.array([]))

print("Total frames parsed:", numFramesParsed)
###############################################################################
# RANGE VS TIME DATA STRUCTURE
###############################################################################

frame_period = 0.1  # seconds (adjust if you know actual frameCfg)
time_axis = []
range_frames = []

for i, points in enumerate(frames_points):

    current_time = i * frame_period
    time_axis.append(current_time)

    if len(points) > 0:
        ranges = np.sqrt(
            points[:,0]**2 +
            points[:,1]**2 +
            points[:,2]**2
        )
        range_frames.append(ranges)
    else:
        range_frames.append(np.array([]))

time_axis = np.array(time_axis)
###############################################################################
# 3D ANIMATION
###############################################################################
fig = plt.figure()
ax = fig.add_subplot(111, projection='3d')

scatter = ax.scatter([], [], [], c=[], cmap='jet')

ax.set_xlim(-5, 5)
ax.set_ylim(0, 10)
ax.set_zlim(-3, 3)

ax.set_xlabel("X (m)")
ax.set_ylabel("Y (m)")
ax.set_zlabel("Z (m)")
ax.set_title("mmWave Point Cloud Motion")

def update(frame_idx):

    ax.cla()

    points = frames_points[frame_idx]
    velocities = frames_velocity[frame_idx]

    ax.set_xlim(-5, 5)
    ax.set_ylim(0, 10)
    ax.set_zlim(-3, 3)

    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_zlabel("Z (m)")
    ax.set_title(f"Frame {frame_idx}")

    if len(points) > 0:
        sc = ax.scatter(
            points[:,0],
            points[:,1],
            points[:,2],
            c=velocities,
            cmap='jet'
        )

    return []

ani = FuncAnimation(
    fig,
    update,
    frames=len(frames_points),
    interval=50
)

plt.show()

###############################################################################
# STATIC RANGE VS TIME PLOT
###############################################################################

plt.figure()

for i in range(len(range_frames)):
    if len(range_frames[i]) > 0:
        t = np.ones(len(range_frames[i])) * time_axis[i]
        plt.scatter(t, range_frames[i], s=5)

plt.xlabel("Time (s)")
plt.ylabel("Range (m)")
plt.title("Range vs Time")
plt.grid()
plt.show()

###############################################################################
# ANIMATED RANGE VS TIME
###############################################################################

fig2, ax2 = plt.subplots()

ax2.set_xlim(0, time_axis[-1])
ax2.set_ylim(0, 10)

ax2.set_xlabel("Time (s)")
ax2.set_ylabel("Range (m)")
ax2.set_title("Range vs Time Animation")

all_times = []
all_ranges = []

def update_range(frame_idx):

    if len(range_frames[frame_idx]) > 0:
        t = np.ones(len(range_frames[frame_idx])) * time_axis[frame_idx]
        all_times.extend(t)
        all_ranges.extend(range_frames[frame_idx])

    ax2.cla()
    ax2.set_xlim(0, time_axis[-1])
    ax2.set_ylim(0, 10)
    ax2.set_xlabel("Time (s)")
    ax2.set_ylabel("Range (m)")
    ax2.set_title("Range vs Time Animation")

    ax2.scatter(all_times, all_ranges, s=5)

ani2 = FuncAnimation(
    fig2,
    update_range,
    frames=len(range_frames),
    interval=50
)

plt.show()

#velocity vs time
###############################################################################
# VELOCITY VS TIME PLOTS
###############################################################################

# 1️⃣ Velocity of all detected objects over time (scatter)
plt.figure(figsize=(10,5))
all_velocities = []
all_times = []

for i, vels in enumerate(frames_velocity):
    if len(vels) > 0:
        t = np.ones(len(vels)) * time_axis[i]
        all_times.extend(t)
        all_velocities.extend(vels)

plt.scatter(all_times, all_velocities, s=5, c=all_velocities, cmap='viridis')
plt.colorbar(label="Velocity (m/s)")
plt.xlabel("Time (s)")
plt.ylabel("Velocity (m/s)")
plt.title("Velocity of Detected Objects vs Time")
plt.grid(True)
plt.show()

# 2️⃣ Average velocity vs time
avg_velocity = [np.mean(v) if len(v) > 0 else 0 for v in frames_velocity]

plt.figure(figsize=(10,5))
plt.plot(time_axis, avg_velocity, '-o', color='red')
plt.xlabel("Time (s)")
plt.ylabel("Average Velocity (m/s)")
plt.title("Average Velocity vs Time")
plt.grid(True)
plt.show()

# 3️⃣ Maximum velocity vs time
max_velocity = [np.max(v) if len(v) > 0 else 0 for v in frames_velocity]

plt.figure(figsize=(10,5))
plt.plot(time_axis, max_velocity, '-o', color='blue')
plt.xlabel("Time (s)")
plt.ylabel("Maximum Velocity (m/s)")
plt.title("Maximum Velocity vs Time")
plt.grid(True)
plt.show()

# 4️⃣ Velocity histogram per frame (optional)
plt.figure(figsize=(10,5))
for i, vels in enumerate(frames_velocity):
    if len(vels) > 0:
        plt.hist(vels, bins=20, alpha=0.3, label=f"t={time_axis[i]:.2f}s")
plt.xlabel("Velocity (m/s)")
plt.ylabel("Count")
plt.title("Velocity Distribution Over Time")
plt.legend()
plt.show()

import matplotlib.colors as mcolors

all_times = []
all_vels = []
all_ranges = []

# Flatten data for density plotting
for i, vels in enumerate(frames_velocity):
    if len(vels) > 0:
        pts = frames_points[i]
        rngs = np.sqrt(pts[:,0]**2 + pts[:,1]**2 + pts[:,2]**2)
        
        all_times.extend([time_axis[i]] * len(vels))
        all_vels.extend(vels)
        all_ranges.extend(rngs)

# Convert to numpy arrays
all_times = np.array(all_times)
all_vels = np.array(all_vels)
all_ranges = np.array(all_ranges)

# --- Plot A: Micro-Doppler (Velocity vs Time) ---
plt.figure(figsize=(12, 5))
# Using 'turbo' and PowerNorm to highlight subtle arm/leg movements
plt.hist2d(all_times, all_vels, bins=[350, 150], cmap='turbo', 
            norm=mcolors.PowerNorm(gamma=0.4))
plt.colorbar(label='Reflection Density')
plt.title("High-Fidelity Micro-Doppler Signature (VT)")
plt.xlabel("Time (s)")
plt.ylabel("Velocity (m/s)")
plt.grid(alpha=0.2)
plt.show()

# --- Plot B: Range-Time Intensity (RT) ---
plt.figure(figsize=(12, 5))
# LogNorm mimics the dB signal strength of the reference images
plt.hist2d(all_times, all_ranges, bins=[350, 200], cmap='turbo', 
            norm=mcolors.LogNorm())
plt.colorbar(label='Signal Intensity')
plt.title("Range-Time Intensity Profile (RT)")
plt.xlabel("Time (s)")
plt.ylabel("Range (m)")
plt.ylim(0, 10) # Adjust based on your environment
plt.show()