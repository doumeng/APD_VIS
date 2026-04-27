import socket
import struct
import threading
import numpy as np
from collections import defaultdict
from core.parser import DataParser
import config

class UdpReceiver(threading.Thread):
    def __init__(self, ip, port, callback_int_rng, callback_tof, recorder=None):
        super().__init__()
        self.ip = ip
        self.port = port
        self.callback_int_rng = callback_int_rng
        self.callback_tof = callback_tof
        self.recorder = recorder
        self.running = False
        self.paused = False
        self.sock = None
        self.debug = bool(getattr(config, 'RECEIVER_DEBUG', False))
        
        # Fragmentation Handling
        # Buffer: {task_id: {seq: payload}}
        self.fragments = defaultdict(dict)
        self.fragment_counts = defaultdict(int)
        self.expected_fragments = defaultdict(lambda: config.TOTAL_FRAGMENTS)

    def _dbg(self, msg):
        if self.debug:
            print(f"[ReceiverDebug] {msg}")

    def _parse_ctrl(self, ctrl_bytes):
        ctrl_be = struct.unpack('>H', ctrl_bytes)[0]
        ctrl_le = struct.unpack('<H', ctrl_bytes)[0]

        be_len = ctrl_be & 0x1FFF
        le_len = ctrl_le & 0x1FFF

        # Edge C++ struct send is usually host-endian (little-endian on x86).
        # Prefer the variant with plausible payload length.
        if 0 < le_len <= config.DATA_LEN and not (0 < be_len <= config.DATA_LEN):
            return ctrl_le
        if 0 < be_len <= config.DATA_LEN and not (0 < le_len <= config.DATA_LEN):
            return ctrl_be
        # If both plausible, prefer little-endian for compatibility with C++ direct struct send.
        return ctrl_le

    def run(self):
        self.running = True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024 * 8) 
            self.sock.bind((self.ip, self.port))
            print(f"Listening on {self.ip}:{self.port}")
            
            while self.running:
                try:
                    self.sock.settimeout(0.01)
                    data, addr = self.sock.recvfrom(65536) # Max UDP
                    self._dbg(f"pkt from {addr}, len={len(data)}")
                    
                    if len(data) < config.PACKET_SIZE: # Too short
                        self._dbg(f"drop short packet: len={len(data)}")
                        continue
                        
                    # 1. Parse Header
                    # Header (0-2)
                    if data[0:2] != b'\x55\xaa': # Assuming little endian marker based on user spec "0xAA55" usually means bytes AA, 55 or 55, AA.
                        # Wait, user said "0xAA55".
                        # If network order (Big Endian): b'\xaa\x55'
                        # If Intel order (Little Endian): b'\x55\xaa'
                        # Let's try both or just check hex.
                        # Let's assume standard network byte order first: b'\xaa\x55'
                        if data[0:2] != b'\xaa\x55':
                            self._dbg(f"drop bad header: {data[0:2].hex()}")
                            continue # Invalid header

                    # Control (2-4)
                    ctrl = self._parse_ctrl(data[2:4])
                    is_frag = (ctrl & 0x8000) >> 15
                    frag_len = ctrl & 0x1FFF
                    
                    # Task ID (4-7) - 3 bytes
                    # Python struct doesn't do 3-byte int well.
                    # Pack 4 bytes with 0 padding?
                    task_id_bytes = b'\x00' + data[4:7]
                    task_id = struct.unpack('>I', task_id_bytes)[0]
                    
                    # Type (7-8)
                    task_type = data[7]
                    
                    # Seq (8-9)
                    seq = data[8]
                    
                    # Servo status (9-13)
                    pitch_raw = struct.unpack('<h', data[config.OFFSET_PITCH:config.OFFSET_PITCH+2])[0]
                    yaw_raw = struct.unpack('<h', data[config.OFFSET_YAW:config.OFFSET_YAW+2])[0]
                    pitch = pitch_raw / 100.0
                    yaw = yaw_raw / 100.0

                    # Decide expected fragments when first fragment of task arrives
                    if self.fragment_counts[task_id] == 0:
                        if task_type == config.TASK_TYPE_TOF:
                            self.expected_fragments[task_id] = config.TOF_TOTAL_FRAGMENTS
                        else:
                            self.expected_fragments[task_id] = config.TOTAL_FRAGMENTS
                    
                    # Data Payload (9-4105)
                    payload = data[config.OFFSET_DATA:config.OFFSET_DATA + config.DATA_LEN]

                    # Tail check: accept both byte orders
                    tail = data[-2:]
                    if tail not in (b'\x55\xAA', b'\xAA\x55'):
                        if task_type == config.TASK_TYPE_TOF:
                            self._dbg(f"drop tof bad tail: task={task_id}, seq={seq}, tail={tail.hex()}")
                        continue

                    expected = self.expected_fragments[task_id]

                    if seq >= expected:
                        if task_type == config.TASK_TYPE_TOF:
                            self._dbg(f"drop tof bad seq: task={task_id}, seq={seq}, total={expected}")
                        continue

                    if frag_len == 0 or frag_len > config.DATA_LEN:
                        if task_type == config.TASK_TYPE_TOF:
                            self._dbg(f"drop tof bad frag_len: task={task_id}, seq={seq}, frag_len={frag_len}")
                        continue
                    
                    # Checksum (4105)
                    # TODO: Implement XOR Checksum validation
                    
                    # Reassembly Logic
                    if seq not in self.fragments[task_id]:
                        self.fragments[task_id][seq] = payload
                        self.fragment_counts[task_id] += 1
                        if seq == 0:
                            self.fragments[task_id]['servo'] = (pitch, yaw)
                    elif task_type == config.TASK_TYPE_TOF:
                        self._dbg(f"duplicate tof fragment: task={task_id}, seq={seq}")
                    
                    if self.fragment_counts[task_id] == expected:
                        # Reassemble
                        full_data = b''.join(self.fragments[task_id][i] for i in range(expected))
                        if task_type == config.TASK_TYPE_TOF:
                            self._dbg(f"tof reassembled: task={task_id}, fragments={expected}, bytes={len(full_data)}")
                        
                        
                        if self.paused:
                             # Cleanup and continue
                            del self.fragments[task_id]
                            del self.fragment_counts[task_id]
                            del self.expected_fragments[task_id]
                            continue

                        # Record Raw Data if enabled
                        if self.recorder and self.recorder.recording:
                            servo = self.fragments[task_id].get('servo', (0.0, 0.0))
                            # Pass data and type separately. Recorder will add type byte to file.
                            self.recorder.write_frame(full_data, task_type, servo)

                        # Process
                        servo = self.fragments[task_id].get('servo', (0.0, 0.0))
                        if task_type == 0: # Int + Rng
                            intensity, rng = DataParser.parse_intensity_range(full_data)
                            self.callback_int_rng(intensity, rng, task_id, servo[0], servo[1])
                            
                        elif task_type == 1: # ToF
                            tof = DataParser.parse_tof(full_data)
                            self.callback_tof(tof, task_id, servo[0], servo[1])
                            
                        # Cleanup
                        del self.fragments[task_id]
                        del self.fragment_counts[task_id]
                        del self.expected_fragments[task_id]
                        
                        # Cleanup old fragments (TODO: Implement timeout cleanup)

                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"Receiver Error: {e}")
                    self._dbg("exception branch reached")
                    
        except Exception as e:
            print(f"Socket Bind Error: {e}")
        finally:
            if self.sock:
                self.sock.close()

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
