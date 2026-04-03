import serial
import serial.tools.list_ports
import struct
import threading
import time
from PyQt5.QtCore import QObject, pyqtSignal

class SerialProtocol:
    """
    Handles RS422 Frame Construction and Parsing.
    Frame Format:
    Header(4) | Addr(1) | Type(1) | Seq(4) | Cmd(1) | DataL(1) | DataH(1) | Reserved(13) | FCS(2) | Tail(4)
    Total Length: 32 bytes
    little-endian for multi-byte fields (Seq, FCS).
    """
    HEADER = 0xBABABABA
    TAIL = 0xBABABABA
    ADDR = 0x56
    TYPE = 0x46 # Preprocessing Module
    FRAME_LEN = 32

    def __init__(self):
        self.seq_num = 0

    def calculate_checksum(self, data_bytes):
        """
        Checksum range: 5-(n-6) bytes.
        For 32 bytes frame: Index 4 to 25 (0-based indexing for bytes 5-26).
        Note: The protocol says "Bytes 1-4 is Header". So Byte 5 is index 4.
        Total 32 bytes. n-6 = 26 bytes.
        So verify range: Index 4 (Addr) to Index 25 (Last Reserved).
        Wait, "5-(n-6)" likely means from byte 5 up to byte (n-6).
        Byte 1 is index 0. Byte 5 is index 4.
        Frame length n=32. n-6 = 26.
        So sum form index 4 to index 25 (inclusive).
        Bytes 27-28 are FCS (Index 26-27).
        """
        # Sum of bytes from index 4 to 25
        # Ensure input is at least 26 bytes long if partial
        chk_sum = sum(data_bytes[4:26])
        return chk_sum & 0xFFFF

    def pack_command(self, cmd, *data_bytes):
        """
        Constructs a command frame.
        data_bytes: sequence of bytes to fill from index 11 onwards.
        """
        self.seq_num += 1
        
        payload = bytearray(32)
        struct.pack_into('<I', payload, 0, self.HEADER) # 0-3
        payload[4] = self.ADDR
        payload[5] = self.TYPE
        struct.pack_into('<I', payload, 6, self.seq_num) # 6-9
        payload[10] = cmd
        
        # Fill data bytes starting at index 11
        for i, b in enumerate(data_bytes):
            if 11 + i < 26: # Ensure within data/reserved range
                payload[11 + i] = b
        
        # Calculate Checksum
        fcs = self.calculate_checksum(payload)
        struct.pack_into('<H', payload, 26, fcs) # 26-27
        
        struct.pack_into('<I', payload, 28, self.TAIL) # 28-31
        
        return payload

    def parse_response(self, data):
        """
        Parses a 32-byte response frame.
        Returns a dict with parsed values or None if invalid.
        """
        if len(data) != 32:
            return None
        
        # Validate Header/Tail
        header, = struct.unpack_from('<I', data, 0)
        tail, = struct.unpack_from('<I', data, 28)
        if header != self.HEADER or tail != self.TAIL:
            return None
            
        # Validate Checksum
        calc_fcs = self.calculate_checksum(data)
        recv_fcs, = struct.unpack_from('<H', data, 26)
        
        if calc_fcs != recv_fcs:
            print(f"Checksum Error: Calc {calc_fcs:04X} != Recv {recv_fcs:04X}")
            # return None # Optionally ignore checksum for debugging if needed

        # Parse Fields
        # Table 3-27
        # Byte 11 (Index 10): Res (Cmd Result) -> Actually Command ID (Table 3-28)
        # Byte 12 (Index 11): Val1 -> Actually Result/Status (Table 3-28)
        # Byte 13 (Index 12): Val2
        # Byte 14 (Index 13): Val3
        # Byte 15 (Index 14): Val4
        # Byte 16-17 (Index 15-16): Temp
        # Byte 18-19 (Index 17-18): Voltage
        
        cmd_id = data[10]
        res_val = data[11]
        val2 = data[12]
        val3 = data[13]
        val4 = data[14]
        
        temp_raw, = struct.unpack_from('<H', data, 15)
        volt_raw, = struct.unpack_from('<H', data, 17)
        
        # Conversions
        temp_c = temp_raw / 10.0
        # Volt: Byte 18 Int, Byte 19 Dec
        volt_v = data[17] + data[18] / 10.0
        
        return {
            'cmd_id': cmd_id,
            'res_val': res_val, 
            'val2': val2, 'val3': val3, 'val4': val4,
            'temp': temp_c,
            'volt': volt_v,
            'raw': data
        }

class SerialWorker(QObject):
    sig_received_frame = pyqtSignal(object) # dict
    sig_status_update = pyqtSignal(str) # msg
    sig_log = pyqtSignal(str) # Log message (TX/RX)

    def __init__(self):
        super().__init__()
        self.serial = None
        self.protocol = SerialProtocol()
        self.running = False
        self.thread = None

    def open_port(self, port, baud=115200):
        if self.serial and self.serial.is_open:
            self.close_port()
        
        try:
            self.serial = serial.Serial(port, baud, timeout=0.1)
            self.running = True
            self.thread = threading.Thread(target=self._recv_loop, daemon=True)
            self.thread.start()
            self.sig_status_update.emit(f"Connected to {port}")
            self.sig_log.emit(f"Open {port} @ {baud}")
            return True
        except Exception as e:
            self.sig_status_update.emit(f"Error: {e}")
            self.sig_log.emit(f"Failed to open {port}: {e}")
            return False

    def close_port(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.sig_status_update.emit("Disconnected")
            self.sig_log.emit("Port Closed")

    def send_command(self, cmd, *data):
        if not self.serial or not self.serial.is_open:
            self.sig_log.emit("Error: Serial not open")
            return

        frame = self.protocol.pack_command(cmd, *data)
        try:
            self.serial.write(frame)
            self.sig_log.emit(f"TX: {frame.hex().upper()}")
        except Exception as e:
            self.sig_log.emit(f"TX Error: {e}")

    def _recv_loop(self):
        buffer = bytearray()
        while self.running and self.serial and self.serial.is_open:
            try:
                # Read chunks
                chunk = self.serial.read(32)
                if chunk:
                    buffer.extend(chunk)
                
                # Try to find frame
                while len(buffer) >= 32:
                    idx = -1
                    if buffer[0:4] == b'\xBA\xBA\xBA\xBA':
                        # Check tail
                        if buffer[28:32] == b'\xBA\xBA\xBA\xBA':
                            # Full frame candidate
                            frame_data = buffer[:32]
                            parsed = self.protocol.parse_response(frame_data)
                            if parsed:
                                self.sig_received_frame.emit(parsed)
                                # Only log if cmd_id is not 0x00
                                if parsed['cmd_id'] != 0x00:
                                    self.sig_log.emit(f"RX: {frame_data.hex().upper()}")
                                buffer = buffer[32:] # Remove frame
                                continue
                            else:
                                self.sig_log.emit(f"RX Bad Checksum: {frame_data.hex().upper()}")
                                buffer = buffer[32:]
                                continue
                        else:
                            buffer.pop(0)
                    else:
                        # Drop 1 byte
                        buffer.pop(0)
                        
            except Exception as e:
                print(f"Serial RX Error: {e}")
                time.sleep(1)

