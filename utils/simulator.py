import socket
import struct
import time
import numpy as np
import threading

# Configuration
TARGET_IP = "127.0.0.1"
TARGET_PORT = 5005
IMG_WIDTH = 128
IMG_HEIGHT = 128
PIXEL_COUNT = IMG_WIDTH * IMG_HEIGHT
BYTES_PER_PIXEL = 4 # 2 bytes Range + 2 bytes Intensity
FRAME_SIZE = PIXEL_COUNT * BYTES_PER_PIXEL # 65536 bytes
FRAGMENT_SIZE = 4096
FRAGMENTS_PER_FRAME = FRAME_SIZE // FRAGMENT_SIZE # 16

class UdpSimulator:
    def __init__(self, ip=TARGET_IP, port=TARGET_PORT):
        self.ip = ip
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.running = False
        self.task_id_counter = 0

    def start(self, mode='int_rng', interval=0.02):
        self.running = True
        print(f"Simulator started. Sending to {self.ip}:{self.port} (Mode: {mode})")
        
        t = 0
        while self.running:
            try:
                t += 0.1
                self.task_id_counter = (self.task_id_counter + 1) % 0xFFFFFF
                
                # 1. Generate Frame Data (65536 bytes)
                if mode == 'int_rng':
                    # Pattern: Moving sine wave
                    x = np.linspace(0, 10, IMG_WIDTH)
                    y = np.linspace(0, 10, IMG_HEIGHT)
                    X, Y = np.meshgrid(x, y)
                    
                    # Range (Real value 0-50m -> Raw 0-500)
                    rng_real = (np.sin(X + t) + 1) * 25 # 0-50
                    rng_raw = (rng_real * 10).astype(np.uint16)
                    
                    # Intensity (0-200)
                    intensity = ((np.cos(Y - t) + 1) * 100).astype(np.uint16)
                    
                    # Interleave: [R, I, R, I...]
                    # Flatten
                    rng_flat = rng_raw.flatten()
                    int_flat = intensity.flatten()
                    
                    # Stack and reshape to (N, 2) then flatten to interleave
                    interleaved = np.stack((rng_flat, int_flat), axis=1).flatten()
                    
                    # Convert to bytes (Little Endian <H)
                    # Note: Protocol didn't specify endianness of data, assuming Little Endian for standard PC/ARM
                    payload_bytes = interleaved.astype('<u2').tobytes()
                    task_type = 0
                    
                else: # ToF
                    # Generate 1 frame of 128x128, each pixel uint16 in range [0, 8000]
                    # Total: 128 * 128 * 2 bytes = 32768 bytes
                    tof_frame = np.random.randint(0, 8001, (IMG_HEIGHT, IMG_WIDTH), dtype=np.uint16)
                    payload_bytes = tof_frame.astype('<u2').tobytes()
                    task_type = 1

                # 2. Fragment and Send
                total_fragments = len(payload_bytes) // FRAGMENT_SIZE
                
                for seq in range(total_fragments):
                    # Slice data
                    start_idx = seq * FRAGMENT_SIZE
                    end_idx = start_idx + FRAGMENT_SIZE
                    chunk = payload_bytes[start_idx:end_idx]
                    
                    # Construct Packet
                    # Header: 0xAA55 (Big Endian: \xAA\x55)
                    header = b'\xAA\x55'
                    
                    # Control: D15=1 (Fragment), Length=4096 (0x1000) -> 0x9000
                    # Big Endian
                    ctrl = struct.pack('>H', 0x9000)
                    
                    # Task ID: 3 bytes (Big Endian-ish)
                    # struct doesn't support 3 bytes, pack manually
                    tid_bytes = struct.pack('>I', self.task_id_counter)[1:4]
                    
                    # Type: 1 byte
                    type_byte = struct.pack('B', task_type)
                    
                    # Seq: 1 byte
                    seq_byte = struct.pack('B', seq)
                    
                    # Data: chunk (4096 bytes)
                    
                    # FCS: XOR Checksum of data
                    fcs = 0
                    for b in chunk:
                        fcs ^= b
                    fcs_byte = struct.pack('B', fcs)
                    
                    # End: 0x55AA (Big Endian: \x55\xAA)
                    tail = b'\x55\xAA'
                    
                    packet = header + ctrl + tid_bytes + type_byte + seq_byte + chunk + fcs_byte + tail
                    
                    self.sock.sendto(packet, (self.ip, self.port))
                    
                    # Small sleep between packets to avoid buffer overflow on receiver?
                    # time.sleep(0.0001) 

                time.sleep(interval) # 50Hz = 0.02s
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Error: {e}")
                time.sleep(1)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="UDP Simulator")
    parser.add_argument("--mode", choices=["int_rng", "tof"], default="int_rng", help="Simulation mode (int_rng or tof)")
    parser.add_argument("--rate", type=float, default=0.02, help="Packet interval in seconds (default 0.02 = 50Hz)")
    
    args, unknown = parser.parse_known_args() # Use parse_known_args in case no args

    sim = UdpSimulator()
    print(f"Starting {args.mode} Simulator @ {1/args.rate:.1f} Hz")
    try:
        sim.start(mode=args.mode, interval=args.rate)
    except KeyboardInterrupt:
        print("Stopped.")
