import threading
import queue
import time
import os

class DataRecorder(threading.Thread):
    def __init__(self):
        super().__init__()
        self.save_dir = "." # Default to current directory
        self.file_handle = None
        self.csv_handle = None
        self.frame_index = 0
        self.current_type = None # 0: Depth, 1: ToF
        self.write_queue = queue.Queue()
        self.running = False
        self.recording = False
        self.lock = threading.Lock()
        self.bytes_written = 0
        self.start_time = 0

    def start_recording(self, save_dir):
        with self.lock:
            if self.recording:
                return False
            
            try:
                self.save_dir = save_dir
                if not os.path.exists(save_dir):
                    os.makedirs(save_dir, exist_ok=True)
                
                # Reset state
                self.file_handle = None
                self.csv_handle = None
                self.frame_index = 0
                self.current_type = None
                self.recording = True
                self.bytes_written = 0
                self.start_time = time.time()
                print(f"Recorder started (Waiting for data in {save_dir})...")
                return True
            except Exception as e:
                print(f"Recorder Error: {e}")
                self.stop_recording()
                return False

    def _create_file(self, task_type):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        prefix = "depth" if task_type == 0 else "tof"
        filename = f"{prefix}_{timestamp}.bin"
        filepath = os.path.join(self.save_dir, filename)
        csv_filename = f"{prefix}_{timestamp}.csv"
        csv_filepath = os.path.join(self.save_dir, csv_filename)
        
        try:
            self.file_handle = open(filepath, 'wb')
            self.csv_handle = open(csv_filepath, 'w', encoding='utf-8')
            self.csv_handle.write("frame_index,timestamp,pitch,yaw\n")
            self.current_type = task_type
            print(f"Created recording file: {filepath} and {csv_filepath}")
            return True
        except Exception as e:
            print(f"Failed to create file {filepath}: {e}")
            return False

    def stop_recording(self):
        with self.lock:
            if not self.recording:
                return

            self.recording = False
            time.sleep(0.1) 
            
            if self.file_handle:
                self.file_handle.close()
                self.file_handle = None
                if self.csv_handle:
                    self.csv_handle.close()
                    self.csv_handle = None
                self.current_type = None
            print("Recorder stopped.")
            
    def write_frame(self, data, task_type, servo=(0.0, 0.0)):
        if self.recording:
            self.write_queue.put((data, task_type, servo))

    def run(self):
        self.running = True
        while self.running:
            try:
                # Get data with timeout
                item = self.write_queue.get(timeout=0.5)
                data, task_type, servo = item
                
                with self.lock:
                    # If file not created yet, create it based on first packet type
                    if self.file_handle is None:
                         if not self._create_file(task_type):
                             continue # Skip if file creation failed
                    
                    # Only write if type matches the file type (User: "Only save one type")
                    if self.file_handle and not self.file_handle.closed:
                        if task_type == self.current_type:
                            # Write Data only (No headers, no type byte)
                            self.file_handle.write(data)
                            if self.csv_handle and not self.csv_handle.closed:
                                self.csv_handle.write(f"{self.frame_index},{time.time()},{servo[0]},{servo[1]}\n")
                                self.frame_index += 1
                            self.bytes_written += len(data)
                
                self.write_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Write Error: {e}")

    def close(self):
        self.running = False
        if self.csv_handle:
            self.csv_handle.close()
        self.stop_recording()
        if self.file_handle:
            self.file_handle.close()

    def get_status(self):
        if not self.recording:
            return "Idle", 0
        
        if self.file_handle:
            duration = time.time() - self.start_time
            fname = os.path.basename(self.file_handle.name)
            return f"{fname}", self.bytes_written
        else:
            return "Waiting for data...", 0
