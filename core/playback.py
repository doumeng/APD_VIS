import threading
import time
import os
import struct
import csv
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from core.parser import DataParser
import numpy as np

# Frame Sizes (in bytes)
FRAME_SIZE_DEPTH = 65536  # 128x128 * 2 (Int) + 128x128 * 2 (Rng) = 65536
FRAME_SIZE_TOF = 32768    # 128x128 * 2 (ToF uint16) = 32768

class PlaybackManager(QObject):
    sig_update_int_rng = pyqtSignal(object, object, object, float, float) # intensity, range, task_id, pitch, yaw
    sig_update_tof = pyqtSignal(object, object, float, float) # tof, task_id, pitch, yaw
    sig_progress = pyqtSignal(int, int) # current, total
    sig_finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.filename = None
        self.file_handle = None
        self.csv_data = []
        self.timer = QTimer()
        self.timer.timeout.connect(self.read_next_frame)
        self.total_frames = 0
        self.current_frame = 0
        self.paused = False
        self.frame_size = 0
        self.data_type = 0 # 0: Depth, 1: ToF

    def load_file(self, filename):
        if not os.path.exists(filename):
            return False
        
        self.filename = filename
        
        # Determine Type and Frame Size from Filename Prefix
        fname = os.path.basename(filename).lower()
        if fname.startswith("tof_"):
            self.data_type = 1
            self.frame_size = FRAME_SIZE_TOF
        elif fname.startswith("depth_"):
            self.data_type = 0
            self.frame_size = FRAME_SIZE_DEPTH
        else:
            # Fallback based on file size or try both?
            # Default to Depth for safety if unknown
            self.data_type = 0
            self.frame_size = FRAME_SIZE_DEPTH
            print(f"Warning: Unknown file prefix '{fname}'. Defaulting to Depth mode.")

        try:
            self.file_handle = open(filename, 'rb')
            
            csv_filename = os.path.splitext(filename)[0] + '.csv'
            self.csv_data = []
            if os.path.exists(csv_filename):
                try:
                    with open(csv_filename, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            self.csv_data.append({
                                'pitch': float(row.get('pitch', 0.0)),
                                'yaw': float(row.get('yaw', 0.0))
                            })
                except Exception as e:
                    print(f"Error reading CSV: {e}")
            
            # Calculate total frames
            self.file_handle.seek(0, os.SEEK_END)
            file_size = self.file_handle.tell()
            if self.frame_size > 0:
                self.total_frames = file_size // self.frame_size
            else:
                self.total_frames = 0
                
            self.file_handle.seek(0)
            self.current_frame = 0
            return True
        except Exception as e:
            print(f"Error opening file: {e}")
            return False

    def start(self, interval_ms=20):
        if not self.file_handle:
            return
        self.paused = False
        self.timer.start(interval_ms)

    def pause(self):
        self.paused = True
        self.timer.stop()
        
    def stop(self):
        self.paused = False
        self.timer.stop()
        if self.file_handle:
            self.file_handle.seek(0)
            self.current_frame = 0
            
    def seek(self, frame_idx):
        if not self.file_handle:
            return
        
        frame_idx = max(0, min(frame_idx, self.total_frames - 1))
        self.current_frame = frame_idx
        self.file_handle.seek(frame_idx * self.frame_size)
        self.sig_progress.emit(self.current_frame, self.total_frames)

    def read_next_frame(self):
        if not self.file_handle:
            return

        # Read Frame Data (Raw Payload)
        data = self.file_handle.read(self.frame_size)
        
        if len(data) < self.frame_size:
            self.stop()
            self.sig_finished.emit()
            return
            
        pitch, yaw = 0.0, 0.0
        if self.current_frame < len(self.csv_data):
            pitch = self.csv_data[self.current_frame]['pitch']
            yaw = self.csv_data[self.current_frame]['yaw']

        # Parse based on Type
        if self.data_type == 0: # Depth (Int + Rng)
            intensity, rng = DataParser.parse_intensity_range(data)
            # self.sig_update_int_rng.emit(intensity, rng)
            self.sig_update_int_rng.emit(np.rot90(intensity, -1), np.rot90(rng, -1), None, pitch, yaw) # Rotate 90 degrees clockwise for correct orientation   
        elif self.data_type == 1: # ToF
            tof = DataParser.parse_tof(data)
            self.sig_update_tof.emit(tof, None, pitch, yaw)
            
        self.current_frame += 1
        self.sig_progress.emit(self.current_frame, self.total_frames)

    def close(self):
        self.stop()
        if self.file_handle:
            self.file_handle.close()
