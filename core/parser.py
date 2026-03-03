import numpy as np
import struct
from config import *

class DataParser:
    @staticmethod
    def parse_intensity_range(raw_data):
        """
        Parse raw bytes (65536 bytes) into Intensity and Range images.
        Format: Interleaved [Range(uint16), Intensity(uint16)]
        Range: First 2 bytes (Little/Big Endian? Assume Little for now)
        Intensity: Next 2 bytes
        """
        # Convert bytes to uint16 array
        # assuming Little Endian (<H) based on typical embedded systems
        # User didn't specify endianness, start with Little Endian (<)
        data_u16 = np.frombuffer(raw_data, dtype=np.uint16)
        
        # Reshape to (Pixel Count, 2)
        # Col 0: Range, Col 1: Intensity
        pixels = data_u16.reshape(-1, 2)
        
        range_raw = pixels[:, 0]
        intensity_raw = pixels[:, 1]
        
        # Scaling
        range_img = range_raw.reshape((IMG_HEIGHT, IMG_WIDTH)).astype(np.float32) / RANGE_SCALE_FACTOR
        intensity_img = intensity_raw.reshape((IMG_HEIGHT, IMG_WIDTH)).astype(np.float32)
        
        return intensity_img, range_img

    @staticmethod
    def parse_tof(raw_data):
        """
        Parse raw bytes (32768 bytes) into ToF image.
        Format: uint16 per pixel (128x128 = 16384 pixels)
        """
        # 16384 * 2 bytes = 32768 bytes
        if len(raw_data) != 32768:
            # Fallback or error handling
            # Maybe it's still 65536 bytes (2 frames)? 
            # Or maybe padded?
            # Let's try to slice or pad if needed, but ideally it should match.
            pass

        data_u16 = np.frombuffer(raw_data, dtype=np.uint16)[0:PIXEL_COUNT] # Take only the first 16384 values
        tof_img = data_u16.reshape((IMG_HEIGHT, IMG_WIDTH)).astype(np.float32)
        return tof_img
