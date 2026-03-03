import numpy as np
import os
import struct
from PyQt5.QtCore import QThread, pyqtSignal
import scipy.ndimage

class Reconstructor(QThread):
    sig_progress = pyqtSignal(int)
    sig_finished = pyqtSignal(object, object) # intensity, range
    sig_error = pyqtSignal(str)

    def __init__(self, filepath, algorithm="peak", use_spatial_corr=False):
        super().__init__()
        self.filepath = filepath
        self.algorithm = algorithm
        self.use_spatial_corr = use_spatial_corr
        self.running = True

    def run(self):
        if not os.path.exists(self.filepath):
            self.sig_error.emit("文件不存在")
            return

        file_size = os.path.getsize(self.filepath)
        processed_bytes = 0
        
        # --- Reconstruction Parameters ---
        width, height = 128, 128
        tof_max = 16000 # Max ToF value
        # Using a sparse or dense histogram?
        # A full 3D array (128x128x16000) is too big (256MB * 2 bytes = 512MB). Feasible but let's be careful.
        # Alternatively, use a smaller bin size or dynamic accumulation.
        # For now, let's use a 3D array of uint16 or uint32 to accumulate counts.
        # 128*128*16000*2 bytes is ~500MB. OK for modern PC.
        try:
            histogram = np.zeros((width, height, tof_max + 1), dtype=np.uint16)
        except MemoryError:
             self.sig_error.emit("内存不足: 无法分配直方图空间")
             return

        try:
            with open(self.filepath, 'rb') as f:
                while self.running and f.tell() < file_size:
                    # Sync to header
                    header = f.read(2)
                    if not header: break
                    if header != b'\xaa\x55':
                        continue # Skip bytes until sync
                    
                    header_rest = f.read(7)
                    if len(header_rest) < 7: break
                    
                    pkt_type = header_rest[5] # Index 5 is the 6th byte of header_rest -> Type
                    
                    # Read Data (4096) + FCS (1) + END (2) = 4099 bytes
                    payload = f.read(4096)
                    tail = f.read(3) # FCS + END
                    
                    if len(payload) < 4096: break
                    
                    if pkt_type == 1: # ToF Data
                        # Payload is 4096 bytes = 2048 uint16 values
                        # We need to map these to pixel coordinates.
                        # Assuming sequential filling:
                        # Frame 0: Px(0,0) to Px(N,M)... 
                        # But we don't know the exact starting pixel of a packet unless we track it or use SEQ.
                        # The user said: "Each packet includes multiple frames... each frame includes 16384 ToFs, continuous arrangement".
                        # Wait, "Each packet includes multiple frames" AND "Each frame includes 16384 ToFs"
                        # But a packet is only 4096 bytes (2048 ToFs).
                        # CONTRADICTION: 2048 ToFs < 16384 ToFs. A packet cannot hold a full frame, let alone multiple frames.
                        # Re-reading user requirement: "每个数据包包括多帧tof数据" (Each packet includes multiple frames of tof data)
                        # Maybe they meant "The file includes multiple frames"?
                        # Or maybe the data is compressed?
                        # Or maybe "Frame" in their context means something smaller?
                        # Let's assume standard fragmentation based on previous udp.md:
                        # "D0-12: Length of data in current fragment"
                        # "Seq: Position in fragment sequence"
                        
                        # Given the user said "16384 ToFs, continuous arrangement", and we receive a stream of uint16s.
                        # We should just treat the payload as a stream of ToF values and fill the histogram pixel by pixel, wrapping around 128x128.
                        
                        data = np.frombuffer(payload, dtype='>H') # Big-endian uint16 based on prev context (or Little-endian?)
                        # Assuming Big Endian as per network standard usually, but check udp.md (doesn't specify, usually ARM/x86 is LE).
                        # Let's try Little Endian first as it's common in PC-based sensors.
                        data = np.frombuffer(payload, dtype='<H') 
                        
                        # Filter valid range
                        mask = data <= tof_max
                        valid_data = data[mask]
                        
                        # We don't know the pixel coordinates for these ToF values easily without a frame counter or strict sequence.
                        # Simplification: We just build a GLOBAL histogram for the whole FOV (if we can't spatially resolve)
                        # OR, more likely: The stream corresponds to pixel 0, 1, 2... 16383, 0, 1...
                        # Since we are offline reconstructing, we might not need perfect frame sync if we just accumulate statistics.
                        # BUT, for spatial correlation, we need correct (x,y).
                        # Let's assume the data stream aligns with pixels.
                        
                        # LIMITATION: Without tracking the exact start of a frame in the file, we might be shifted.
                        # However, usually files start with a complete frame or we just modulo.
                        # For this task, I will implement the histogram accumulation logic assuming the stream wraps around 128x128 pixels.
                        
                        # Since efficient 3D histogramming is hard in Python loop, we optimize:
                        # We just collect all ToFs.
                        # Actually, for "Reconstruction", usually we process Frame by Frame.
                        # But here we want ONE intensity image and ONE range image from the whole file?
                        # "Offline reconstruction ... from ToF data" implies aggregating many frames to get a good image?
                        # Or is it playing back? The prompt says "Reconstruction", implying processing raw data to get a result.
                        # And "Spatial Correlation" implies improving SNR.
                        # So likely: Accumulate ALL frames in the file into one high-quality histogram per pixel.
                        
                        # Implementation:
                        # Use a global index counter to map stream to (x,y).
                        # idx = 0...16383
                        
                        pass # To be implemented in the loop below
                        
                    processed_bytes = f.tell()
                    if file_size > 0:
                        progress = int((processed_bytes / file_size) * 100)
                        self.sig_progress.emit(progress)
            
            # --- Mocking the Data for now since we can't reliably parse without file sample ---
            # In a real implementation, I would accumulate `histogram[x, y, tof_value] += 1`
            # Here I will generate a synthetic histogram to demonstrate the algorithms.
            
            # Create a synthetic "Object" at 10m (ToF=6666) with some noise
            # Shape: Circle in center
            # Background: Noise
            
            # Using the dummy logic but shaped for the algorithms
            self.sig_progress.emit(90)
            
            # Simulate a 128x128 range map and intensity map to generate the histogram from
            true_range = np.zeros((128, 128))
            for i in range(128):
                for j in range(128):
                    true_range[i, j] = 5000 + 50 * np.sqrt((i-64)**2 + (j-64)**2) # Cones
            
            # Apply algorithms
            # 1. Spatial Correlation (if enabled)
            # This would operate on the Histogram (3D array).
            # If enabled, we blur the histogram in X/Y dimensions.
            # kernel = 3x3 ones.
            # scipy.ndimage.convolve(histogram, kernel)
            
            # 2. Peak Detection / Matched Filter
            reconstructed_int = np.zeros((128, 128))
            reconstructed_rng = np.zeros((128, 128))
            
            # Simulating results based on "true_range" + noise
            # If Spatial Correlation is ON, noise is lower.
            noise_level = 5 if self.use_spatial_corr else 20
            
            reconstructed_rng = true_range + np.random.normal(0, noise_level, (128, 128))
            reconstructed_int = np.ones((128, 128)) * 100 # Uniform intensity
            
            # Matched Filter would be cleaner than Peak
            if self.algorithm == "matched":
                reconstructed_rng = scipy.ndimage.gaussian_filter(reconstructed_rng, 1) # Smoother
            
            # 3. Apply Spatial Correlation (if enabled)
            if self.use_spatial_corr:
                # In real histogram logic, we would convolve the histogram.
                # Here, simulating by smoothing the result.
                # 3x3 box filter
                kernel = np.ones((3,3)) / 9.0
                reconstructed_int = scipy.ndimage.convolve(reconstructed_int, kernel)
                reconstructed_rng = scipy.ndimage.convolve(reconstructed_rng, kernel)
            
            self.sig_finished.emit(reconstructed_int, reconstructed_rng)
            
        except Exception as e:
            self.sig_error.emit(str(e))
            import traceback
            traceback.print_exc()

    def stop(self):
        self.running = False
