import numpy as np
import os
import struct
from PyQt5.QtCore import QThread, pyqtSignal
import scipy.ndimage

class Reconstructor(QThread):
    sig_progress = pyqtSignal(int)
    sig_finished = pyqtSignal(object, object) # intensity, range
    sig_error = pyqtSignal(str)
    sig_global_hist = pyqtSignal(object, object) # x_axis, counts

    def __init__(self, filepath, algorithm="peak", use_spatial_corr=False, params=None, max_frames=0):
        super().__init__()
        self.filepath = filepath
        self.algorithm = algorithm
        self.use_spatial_corr = use_spatial_corr
        self.params = params if params else {}
        self.max_frames = max_frames
        self.running = True

    def run(self):
        if not os.path.exists(self.filepath):
            self.sig_error.emit("文件不存在")
            return

        file_size = os.path.getsize(self.filepath)
        processed_bytes = 0
        
        # --- Reconstruction Parameters ---
        width, height = 128, 128
        tof_max = 8000 # Max ToF value per user request

        try:
            histogram = np.zeros((width, height, tof_max + 1), dtype=np.uint16)
        except MemoryError:
             self.sig_error.emit("内存不足: 无法分配直方图空间")
             return

        try:
            raw_data = np.fromfile(self.filepath, dtype='<H') # Little-endian uint16
            
            total_pixels_per_frame = width * height
            if raw_data.size % total_pixels_per_frame != 0:
                # Handle incomplete frames
                valid_len = (raw_data.size // total_pixels_per_frame) * total_pixels_per_frame
                raw_data = raw_data[:valid_len]
            
            frames = raw_data.reshape(-1, width, height)
            num_frames = frames.shape[0]
            
            if num_frames == 0:
                self.sig_error.emit("文件中没有完整帧数据")
                return

            # Apply Frame Limit
            if self.max_frames > 0 and num_frames > self.max_frames:
                frames = frames[:self.max_frames]
                num_frames = self.max_frames

            # 2. Accumulate Histogram & Global Histogram
            global_hist_counts = np.zeros(tof_max + 1, dtype=np.int64)
            
            for x in range(width):
                if not self.running: break
                
                # Emit progress (0-60%)
                progress = int((x / width) * 60)
                self.sig_progress.emit(progress)
                
                for y in range(height):
                    pixel_vals = frames[:, x, y]
                    
                    # Filter invalid values
                    mask = pixel_vals <= tof_max
                    valid_vals = pixel_vals[mask]
                    
                    if valid_vals.size > 0:
                        counts = np.bincount(valid_vals, minlength=tof_max + 1)
                        if len(counts) > tof_max + 1:
                            counts = counts[:tof_max + 1]
                        counts = counts.astype(np.int64, copy=False)
                        
                        histogram[x, y, :] = counts.astype(np.uint16)
                        global_hist_counts += counts

            # Emit Global Histogram
            x_axis = np.arange(tof_max + 1)
            global_hist_counts[:50] = 0
            global_hist_counts[-50:] = 0
            self.sig_global_hist.emit(x_axis, global_hist_counts)
            
            # Free frames memory
            del frames
            del raw_data
            import gc
            gc.collect()

            # 3. Apply Histogram-Level Spatial Correlation
            if self.use_spatial_corr:
                self.sig_progress.emit(65)
                k_size = int(self.params.get('spatial_kernel', 3))
                if k_size % 2 == 0: k_size += 1
                
                # Convert to float32
                histogram_f = histogram.astype(np.float32)
                
                # Apply 2D spatial smoothing on the histogram 3D volume
                # uniform_filter calculates MEAN. 
                # To get SUM of neighbors: sum = mean * size
                scipy.ndimage.uniform_filter(histogram_f, size=(k_size, k_size, 1), output=histogram_f, mode='constant')
                
                histogram_f *= (k_size * k_size)
                
                histogram = histogram_f

            # --- Processing Algorithms ---
            self.sig_progress.emit(80)
            
            reconstructed_int = np.zeros((width, height), dtype=np.float32)
            reconstructed_rng = np.zeros((width, height), dtype=np.float32)

            if self.algorithm == "peak":
                # Peak Detection Implementation
                # 1. Mask first 50 and last 50 bins
                # histogram shape: (128, 128, 16001)
                histogram[:, :, :50] = 0
                histogram[:, :, -50:] = 0
                
                # 2. Find max count and index
                # max count = intensity
                # index of max count = ToF
                
                # argmax along the ToF axis (axis 2)
                max_indices = np.argmax(histogram, axis=2) # Shape (128, 128)
                max_counts = np.max(histogram, axis=2)     # Shape (128, 128)
                
                reconstructed_int = max_counts.astype(np.float32)
                tof_map = max_indices.astype(np.float32)
                
                # 3. Convert ToF to Distance
                # Formula: (16000 - 2 * tof) * 0.15
                reconstructed_rng = (16000 - 2 * tof_map) * 0.15
                
                # Handle invalid data
                invalid_mask = (max_counts == 0)
                reconstructed_rng[invalid_mask] = 0
            
            elif self.algorithm == "matched":
                # Matched Filter (Laser Pulse Width)
                # Convolve histogram with a Gaussian kernel corresponding to pulse width
                pulse_width = float(self.params.get('pulse_width', 10))
                
                # Generate Gaussian kernel
                # Assume pulse_width is FWHM or similar. 
                # Sigma = width / 2.355 roughly, or just use width as parameter control.
                # Let's use sigma = pulse_width / 2 for reasonable smoothing.
                sigma = max(0.5, pulse_width / 2.0)
                k_len = int(6 * sigma) + 1
                if k_len < 3: k_len = 3
                
                x = np.arange(k_len) - (k_len - 1) / 2
                kernel = np.exp(-0.5 * (x / sigma)**2)
                kernel /= kernel.sum() # Normalize
                
                # Convolve along time axis (axis 2)
                # usage of convolve1d is efficient
                histogram = scipy.ndimage.convolve1d(histogram, kernel, axis=2)
                
                # Then apply Peak Detection on the smoothed histogram
                histogram[:, :, :50] = 0
                histogram[:, :, -50:] = 0
                
                max_indices = np.argmax(histogram, axis=2)
                max_counts = np.max(histogram, axis=2)
                
                reconstructed_int = max_counts.astype(np.float32)
                tof_map = max_indices.astype(np.float32)
                reconstructed_rng = (16000 - 2 * tof_map) * 0.15
                
                invalid_mask = (max_counts == 0)
                reconstructed_rng[invalid_mask] = 0
                
            elif self.algorithm == "derivative":
                # Derivative method (Step and Threshold)
                step = int(self.params.get('step', 1))
                if step < 1: step = 1
                threshold = float(self.params.get('threshold', 0))
                
                h_start = histogram[:, :, :-step].astype(np.int32)
                h_end = histogram[:, :, step:].astype(np.int32)
                diff = h_end - h_start
                
                if diff.shape[2] > 100:
                    diff[:, :, :50] = 0
                    diff[:, :, -50:] = 0
                
                # Find max derivative (steepest rising edge)
                max_indices = np.argmax(diff, axis=2)
                max_vals = np.max(diff, axis=2)
                
                # Apply threshold
                # If max derivative < threshold, signal is invalid
                valid_mask = max_vals >= threshold
                
                reconstructed_int = max_vals.astype(np.float32)
                tof_map = max_indices.astype(np.float32)
                
                reconstructed_rng = (16000 - 2 * tof_map) * 0.15
                
                # Zeros where invalid
                reconstructed_rng[~valid_mask] = 0
                reconstructed_int[~valid_mask] = 0
            
            self.sig_finished.emit(reconstructed_int, reconstructed_rng)
            
        except Exception as e:
            self.sig_error.emit(str(e))
            import traceback
            traceback.print_exc()

    def stop(self):
        self.running = False
