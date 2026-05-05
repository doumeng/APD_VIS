import struct
import threading
import time
import serial
import serial.tools.list_ports
from PyQt5.QtCore import QObject, pyqtSignal

class SerialProtocol:
    HEADER = 0xBABABABA
    TAIL = 0xBABABABA
    FRAME_LEN = 32
    TEMP_BASE = 200
    def __init__(self):
        self.seq_num = 0
        self.payload_bytes = bytearray(20)

    def reset_payload(self):
        self.seq_num = 0
        self.payload_bytes = bytearray(20)
        
    def set_bias(self, v_int, v_dec):
        self.payload_bytes[0] = v_int & 0xFF # 第8字节
        self.payload_bytes[1] = v_dec & 0xFF # 第7字节
        
        
    def set_apd_config(self, trig, test_point, test_mode):
        val = 0
        if trig: val |= 0x01
        if test_point: val |= 0x02
        if test_mode: val |= 0x04
        self.payload_bytes[2] = val & 0xFF # 第9字节

    def set_algo(self, frames, noise, step, thresh, kernel):
        b10 = ((noise & 0x0F) << 4) | (frames & 0x0F)
        b11 = ((thresh & 0x0F) << 4) | (step & 0x0F)
        b12 = kernel & 0xFF
        self.payload_bytes[3] = b10 # 第10字节
        self.payload_bytes[4] = b11 # 第11字节
        self.payload_bytes[5] = b12 # 第12字节

    def set_power(self, cooler_on, apd_on):
        val = 0
        if cooler_on: val |= 0x01
        if apd_on: val |= 0x02
        self.payload_bytes[6] = val & 0xFF # 第13字节

    def set_proj_info(self, dist, vel):
        struct.pack_into('<H', self.payload_bytes, 7, dist) # 14 15
        struct.pack_into('<H', self.payload_bytes, 9, vel)  # 16 17

    def set_temp(self, temp_val):
        self.payload_bytes[11] = temp_val & 0xFF # 第18字节

    def calculate_checksum(self, data_bytes):
        chk_sum = sum(data_bytes[4:26])
        return chk_sum & 0xFFFF

    def get_periodic_frame(self):
        self.seq_num += 1
        frame_cnt = self.seq_num & 0xFFFF
        
        payload = bytearray(32)
        struct.pack_into('<I', payload, 0, self.HEADER)
        struct.pack_into('<H', payload, 4, frame_cnt)
        payload[6:26] = self.payload_bytes
        
        fcs = self.calculate_checksum(payload)
        struct.pack_into('<H', payload, 26, fcs)
        struct.pack_into('<I', payload, 28, self.TAIL)
        return payload

    def parse_response(self, data):
        if len(data) != 32:
            return None
        
        header, = struct.unpack_from('<I', data, 0)
        tail, = struct.unpack_from('<I', data, 28)
        if header != self.HEADER or tail != self.TAIL:
            return None
            
        calc_fcs = self.calculate_checksum(data)
        recv_fcs, = struct.unpack_from('<H', data, 26)
        
        frame_cnt, = struct.unpack_from('<H', data, 4)
        version = data[6] # 第7字节
        test_status = 0 # 8
        apd_bias_status = data[7] # 9
        apd_ctrl_status = data[8] # 10
        algo_status = data[9] # 11
        power_status = data[10] # 12

        temp = struct.unpack_from('<H', data, 11)[0] / 10.0 # 13-14   
        
        volt_int = data[13] # 14
        volt_dec = data[14] # 15
        volt = volt_int + volt_dec / 10.0
        
        v_major = (version >> 4) & 0x0F
        v_minor = version & 0x0F
        
        return {
            "cmd_id": 0x01,
            "frame_cnt": frame_cnt,
            "version": f"{v_major}.{v_minor}",
            "test_status": test_status,
            "apd_bias_status": apd_bias_status,
            "apd_ctrl_status": apd_ctrl_status,
            "algo_status": algo_status,
            "power_status": power_status,
            "temp": temp,
            "volt": volt,
            "raw": data
        }

class SerialWorker(QObject):
    sig_received_frame = pyqtSignal(object) 
    sig_status_update = pyqtSignal(str) 
    sig_log = pyqtSignal(str) 

    def __init__(self):
        super().__init__()
        self.serial = None
        self.protocol = SerialProtocol()
        self.running = False
        self.thread = None
        self.current_port = ""
        self.current_baud = 0
        
        self.cooler_on = False
        self.apd_on = False
        self._log_fp = None
        self._log_lock = threading.Lock()
        self._log_stream_path = ""

    def get_protocol(self):
        return self.protocol

    def _write_stream_line(self, msg):
        if not self._log_fp:
            return
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        try:
            with self._log_lock:
                if self._log_fp:
                    self._log_fp.write(line)
                    self._log_fp.flush()
        except Exception:
            pass

    def _emit_log(self, msg):
        self.sig_log.emit(msg)
        self._write_stream_line(msg)

    def start_log_stream(self, path):
        self.stop_log_stream()
        with self._log_lock:
            self._log_fp = open(path, "w", encoding="utf-8")
            self._log_stream_path = path
            self._log_fp.write(f"# 串口日志会话\n")
            self._log_fp.write(f"# 开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            if self.current_port:
                self._log_fp.write(f"# 端口: {self.current_port}\n")
            if self.current_baud:
                self._log_fp.write(f"# 波特率: {self.current_baud}\n")
            self._log_fp.flush()
        self._emit_log(f"日志流已启动: {path}")

    def stop_log_stream(self):
        with self._log_lock:
            fp = self._log_fp
            self._log_fp = None
            self._log_stream_path = ""
        if fp:
            try:
                fp.flush()
                fp.close()
            except Exception:
                pass

    def reset_command_state(self):
        self.cooler_on = False
        self.apd_on = False
        self.protocol.reset_payload()
        self.protocol.set_power(False, False)

    def open_port(self, port, baud=115200):
        if self.serial and self.serial.is_open:
            self.close_port()
        
        try:
            self.serial = serial.Serial(port, baud, timeout=0.05)
            self.current_port = str(port)
            self.current_baud = int(baud)
            self.running = True
            self.thread = threading.Thread(target=self._worker_loop, daemon=True)
            self.thread.start()
            self.sig_status_update.emit(f"已连接到 {port}")
            self._emit_log(f"打开串口 {port} @ {baud}")
            return True
        except Exception as e:
            self.sig_status_update.emit(f"错误: {e}")
            self._emit_log(f"打开串口失败 {port}: {e}")
            return False

    def close_port(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None
        
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.sig_status_update.emit("连接已断开")
            self._emit_log("串口已关闭")
        self.serial = None
        self.stop_log_stream()
        self.reset_command_state()

    def set_cooler_on(self, state):
        self.cooler_on = state
        self.protocol.set_power(self.cooler_on, self.apd_on)
        self._emit_log(f"发送状态变更: 制冷机 {'开启' if state else '关闭'}")
        
    def set_apd_on(self, state):
        self.apd_on = state
        self.protocol.set_power(self.cooler_on, self.apd_on)
        self._emit_log(f"发送状态变更: APD {'开启' if state else '关闭'}")

    def _worker_loop(self):
        buffer = bytearray()
        last_send_time = 0
        
        while self.running and self.serial and self.serial.is_open:
            try:
                now = time.time()
                if now - last_send_time >= 0.2:
                    frame = self.protocol.get_periodic_frame()
                    self.serial.write(frame)
                    self._emit_log(f"TX: {frame.hex().upper()}") 
                    last_send_time = now

                if self.serial.in_waiting > 0:
                    chunk = self.serial.read(min(self.serial.in_waiting, 32))
                    if chunk:
                        buffer.extend(chunk)
                else:
                    time.sleep(0.01)
                
                while len(buffer) >= 32:
                    start = buffer.find(b'\xBA\xBA\xBA\xBA')
                    if start < 0:
                        # Keep a short tail in case header spans chunks.
                        del buffer[:-3]
                        break

                    if start > 0:
                        del buffer[:start]

                    if len(buffer) < 32:
                        break

                    if buffer[28:32] != b'\xBA\xBA\xBA\xBA':
                        del buffer[0]
                        continue

                    frame_data = bytes(buffer[:32])
                    parsed = self.protocol.parse_response(frame_data)
                    del buffer[:32]
                    if parsed:
                        self.sig_received_frame.emit(parsed)
                        self._emit_log(f"RX: {frame_data.hex().upper()}")
                        
            except Exception as e:
                time.sleep(0.5)
