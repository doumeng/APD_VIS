import numpy as np
import os
import struct
from PyQt5.QtCore import QThread, pyqtSignal
import scipy.ndimage
import cv2

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

                gate_length = int(self.params.get('step', 8))
                if gate_length < 1: gate_length = 1
                derivative_criterion = float(self.params.get('threshold', 0))

                histogram[:, :, :50] = 0
                histogram[:, :, -50:] = 0

                # Histogram shape: (W, H, Bins)
                W, H, Bins = histogram.shape
                
                num_gates = Bins // gate_length
                hist_truncated = histogram[:, :, :num_gates*gate_length]                
                gate_counts = hist_truncated.reshape(W, H, num_gates, gate_length).sum(axis=3).astype(np.int32)
                
                # Rising edge criterion: Gate[k] - Gate[k-2], k in [2, num_gates-1]
                # diff index i corresponds to gate k=i+2 in gate_counts.
                diff =gate_counts[:, :, :-2] -gate_counts[:, :, 2:]
                
                # 3. Find Max Rising Edge
                max_vals = np.max(diff, axis=2)
                max_pos = np.argmax(diff, axis=2)
                
                # 4. Thresholding
                valid_mask = max_vals >= derivative_criterion
                
                for x in range(W):
                    for y in range(H):
                        if not valid_mask[x, y]:
                            continue
                            
                        k = max_pos[x, y] + 2
                        start_bin = k * gate_length
                        end_bin = (k + 2) * gate_length
                        
                        # Clip end_bin (though usually won't exceed unless k is at end)
                        if end_bin > Bins: end_bin = Bins
                        
                        # Get histogram slice
                        subset_counts = histogram[x, y, start_bin:end_bin].astype(np.float32)
                        total_count = np.sum(subset_counts)
                        
                        if total_count > 0:
                            # Calculate Weighted Mean ToF
                            # Local indices: 0 .. len-1
                            local_indices = np.arange(len(subset_counts))
                            # Global bin indices = start_bin + local_indices
                            weighted_sum = np.sum(subset_counts * (start_bin + local_indices))
                            mean_tof = weighted_sum / total_count
                            
                            reconstructed_int[x, y] = max_vals[x, y]
                            reconstructed_rng[x, y] = (16000 - 2 * mean_tof) * 0.15
                        else:
                            reconstructed_int[x, y] = 0
                            reconstructed_rng[x, y] = 0
            
            # --- Post-Processing ---
            # if self.params.get('post_process', True):
            #     self.sig_progress.emit(90)
            #     reconstructed_int = self._apply_post_process(reconstructed_int)
            #     reconstructed_rng = self._apply_post_process(reconstructed_rng)

            self.sig_finished.emit(np.rot90(reconstructed_int, -1), np.rot90(reconstructed_rng, -1))
            
        except Exception as e:
            self.sig_error.emit(str(e))
            import traceback
            traceback.print_exc()

    def _apply_post_process(self, img):
        # 1. Denoise (Median Blur)
        # Using Median Filter to remove salt-and-pepper noise
        img_denoised = cv2.medianBlur(img, 3)
        
        # 2. Completion (Dilation Filling)
        # Identify invalid pixels (assume <= 0 is invalid/noise)
        mask_invalid = (img_denoised <= 1e-3).astype(np.uint8)
        
        if np.sum(mask_invalid) > 0:
            # Dilate the image to propagate valid neighbor values into holes
            # Since background is 0, max-dilation will bring in positive neighbor values
            kernel = np.ones((3, 3), np.uint8)
            img_dilated = cv2.dilate(img_denoised, kernel, iterations=1)
            
            # Only fill the holes
            img_out = np.where(mask_invalid == 1, img_dilated, img_denoised)
            return img_out
        else:
            return img_denoised

    def stop(self):
        self.running = False
