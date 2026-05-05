import threading
import queue
import time
import os
import config

class DataRecorder(threading.Thread):
    def __init__(self):
        super().__init__()
        self.save_dir = "." # Default to current directory
        self.file_handle = None
        self.csv_handle = None
        self.frame_index = 0
        self.current_type = None # 0: Depth, 1: ToF
        self.write_queue = queue.Queue(maxsize=int(getattr(config, 'RECORDER_QUEUE_MAXSIZE', 256)))
        self.running = False
        self.recording = False
        self.lock = threading.Lock()
        self.bytes_written = 0
        self.start_time = 0
        self.frames_dropped = 0

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
                self.frames_dropped = 0
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
            item = (data, task_type, servo)
            try:
                self.write_queue.put_nowait(item)
            except queue.Full:
                if bool(getattr(config, 'RECORDER_DROP_OLDEST', True)):
                    try:
                        self.write_queue.get_nowait()
                        self.write_queue.task_done()
                    except queue.Empty:
                        pass
                    try:
                        self.write_queue.put_nowait(item)
                    except queue.Full:
                        self.frames_dropped += 1
                else:
                    self.frames_dropped += 1

    def run(self):
        self.running = True
        while self.running:
            item = None
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
                            self.file_handle.flush()
                            if self.csv_handle and not self.csv_handle.closed:
                                self.csv_handle.write(f"{self.frame_index},{time.time()},{servo[0]},{servo[1]}\n")
                                self.csv_handle.flush()
                                self.frame_index += 1
                            self.bytes_written += len(data)
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Write Error: {e}")
            finally:
                if item is not None:
                    self.write_queue.task_done()

    def close(self):
        self.stop_recording()
        # Drain pending writes before stopping thread to avoid tail loss.
        try:
            self.write_queue.join()
        except Exception:
            pass
        self.running = False
        if self.is_alive():
            self.join(timeout=1.0)
        with self.lock:
            if self.csv_handle and not self.csv_handle.closed:
                self.csv_handle.close()
                self.csv_handle = None
            if self.file_handle and not self.file_handle.closed:
                self.file_handle.close()
                self.file_handle = None

    def get_status(self):
        if not self.recording:
            return "Idle", 0
        
        if self.file_handle:
            duration = time.time() - self.start_time
            fname = os.path.basename(self.file_handle.name)
            return f"{fname} | Q:{self.write_queue.qsize()} | Drop:{self.frames_dropped}", self.bytes_written
        else:
            return "Waiting for data...", 0

    def get_metrics(self):
        return {
            'recording': bool(self.recording),
            'queue_size': int(self.write_queue.qsize()),
            'frames_dropped': int(self.frames_dropped),
            'bytes_written': int(self.bytes_written)
        }
