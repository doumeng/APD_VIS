'''
Author: doumeng1026@gmail.com
Date: 2026-04-27 10:30:14
LastEditors: Do not edit
LastEditTime: 2026-05-14 17:27:21
Description:
FilePath: \v3\core\parser.py
'''
import numpy as np
import struct
from config import *

class DataParser:
    @staticmethod
    def parse_intensity_range(raw_data, data_format=DATA_FORMAT_INFO_BOARD):
        """
        Parse raw bytes (65536 bytes) into Intensity and Range images.
        info_board: [range uint16 block][intensity uint8 block]
        preprocess: per-pixel interleaved [intensity uint16][range uint16]
        """
        if data_format == DATA_FORMAT_PREPROCESS:
            data_u16 = np.frombuffer(raw_data, dtype=np.uint16)
            pixels = data_u16[:PIXEL_COUNT * 2].reshape(-1, 2)
            intensity_raw = pixels[:, 0]
            range_raw = pixels[:, 1]
        else:
            half = len(raw_data) // 2
            range_raw = np.frombuffer(raw_data[:half], dtype=np.uint16)[:PIXEL_COUNT]
            intensity_raw = np.frombuffer(raw_data[half:], dtype=np.uint8)[:PIXEL_COUNT]

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
            pass

        data_u16 = np.frombuffer(raw_data, dtype="<H")[0:PIXEL_COUNT] # Take only the first 16384 values
        tof_img = data_u16.reshape((IMG_HEIGHT, IMG_WIDTH)).astype(np.float32)
        return tof_img
