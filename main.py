import sys
import os
import numpy as np
import threading
import time
import socket
from collections import defaultdict
import struct

from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QSpinBox, QPushButton, QCheckBox, QGroupBox, QTabWidget, QSplitter, QFileDialog
from PyQt5.QtGui import QDoubleValidator, QIcon
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer, QEvent
from PyQt5 import uic
import pyqtgraph as pg

import serial.tools.list_ports
from core.parser import DataParser
from core.receiver import UdpReceiver
from core.recorder import DataRecorder
from core.playback import PlaybackManager
from core.reconstructor import Reconstructor
from core.processor import ImageProcessor
from core.serial_protocol import SerialWorker
from utils.theme import apply_dark_theme
from utils.colormaps import get_colormap
import config

def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

# =============================================================================
# Main Window
# =============================================================================
class MainWindow(QMainWindow):
    sig_update_int_rng = pyqtSignal(object, object, object)
    sig_update_tof = pyqtSignal(object, object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("单光子激光雷达上位机 v3.0")
        self.setWindowIcon(QIcon(resource_path("icon.webp")))
        # self.resize(2000, 900)
        
        self.receiver = None
        self.recorder = DataRecorder()
        self.playback = PlaybackManager()
        self.processor = ImageProcessor()
        
        # Store Raw Reconstructed Data for reprocessing
        self.raw_recon_int = None
        self.raw_recon_rng = None
        
        self.recorder.start() # Start recorder thread
        self.receiving = False
        
        self.init_ui()
        
        # Connect signals
        self.sig_update_int_rng.connect(self.update_display_int_rng)
        self.sig_update_tof.connect(self.update_display_tof)
        
        # Initialize Algorithm Settings Logic
        self.init_algo_settings()

        # Playback Signals
        self.playback.sig_update_int_rng.connect(self.update_display_int_rng)
        self.playback.sig_update_tof.connect(self.update_display_tof)
        self.playback.sig_progress.connect(self.update_playback_ui)
        self.playback.sig_finished.connect(self.on_playback_finished)
        
        # Timer for updating status
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(1000)

        # Initialize Serial Logic
        self.last_cmd_name = "--"
        self.serial_worker = SerialWorker()
        self.init_serial_logic()

    def closeEvent(self, event):
        if self.receiver:
            self.receiver.stop()
        if self.recorder:
            self.recorder.close()
        if self.playback:
            self.playback.close()
        if self.serial_worker:
            self.serial_worker.close_port()
        event.accept()

    # ui初始化逻辑
    def init_ui(self):
        # Load UI from file
        try:
            uic.loadUi(resource_path(os.path.join("ui", "mainwindow.ui")), self)
        except Exception as e:
            print(f"Error loading UI: {e}")
            return

        # Setup Splitter Sizes (70% - 30%)
        # Window width is 1800 in UI. 1800 * 0.7 = 1260, 1800 * 0.3 = 540
        self.splitter.setSizes([1260, 540])
        self.splitter.setStretchFactor(0, 7)
        self.splitter.setStretchFactor(1, 3)
        
        # Init FPS Counters
        self.fps_int_last_time = time.time()
        self.fps_tof_last_time = time.time()

        # Access Widgets directly (they are now members of self)
        
        # --- Setup Graphics (PyQtGraph) ---
        # 1. Intensity Image
        self.img_int, self.txt_fps_int = self.setup_image_view(self.glw_int, self.sb_int_min, self.sb_int_max, config.DEFAULT_INTENSITY_CMAP)
        
        # 2. Range Image
        self.img_rng, self.txt_fps_rng = self.setup_image_view(self.glw_rng, self.sb_rng_min, self.sb_rng_max, config.DEFAULT_RANGE_CMAP)
        
        # 3. ToF Image
        self.img_tof, self.txt_fps_tof = self.setup_image_view(self.glw_tof, self.sb_tof_min, self.sb_tof_max, config.DEFAULT_TOF_CMAP)
        
        # 4. Histograms
        # plot_hist_int, plot_hist_rng, plot_hist_tof are promoted PlotWidgets
        self.hist_int = self.plot_hist_int.getPlotItem()
        self.hist_rng = self.plot_hist_rng.getPlotItem()
        self.hist_tof = self.plot_hist_tof.getPlotItem()
        
        self.hist_int.setTitle("强度分布直方图")
        self.hist_rng.setTitle("距离分布直方图")
        self.hist_tof.setTitle("ToF 统计直方图")

        # --- Connect Signals ---
        # Net
        self.btn_conn.clicked.connect(self.toggle_connect)
        
        # Rec
        self.btn_rec.clicked.connect(self.toggle_record)
        self.btn_set_rec_path.clicked.connect(self.select_recording_path)
        
        # Playback
        self.btn_load.clicked.connect(self.load_playback_file)
        self.btn_play.clicked.connect(self.toggle_playback_or_stream)
        
        # Reconstruction
        self.btn_reconstruct.clicked.connect(self.start_reconstruction)
        
        # Mouse Clicks
        try:
            self.img_int.scene().sigMouseClicked.connect(lambda evt: self.on_image_click(evt, self.img_int, "Intensity"))
            self.img_rng.scene().sigMouseClicked.connect(lambda evt: self.on_image_click(evt, self.img_rng, "Range"))
            self.img_tof.scene().sigMouseClicked.connect(lambda evt: self.on_image_click(evt, self.img_tof, "ToF"))
        except AttributeError:
            print("Warning: Could not connect mouse click events (scene not ready?)")

    # 算法设置UI初始化和更新逻辑
    def init_algo_settings(self):
        # Initial UI State based on processor defaults
        self.chk_dbscan.setChecked(self.processor.settings['dbscan_enabled'])
        self.sb_dbscan_eps.setValue(self.processor.settings['dbscan_eps'])
        self.sb_dbscan_min_points.setValue(self.processor.settings['dbscan_min_points'])
        
        self.chk_range_gate.setChecked(self.processor.settings['range_gate_enabled'])
        self.sb_range_min.setValue(self.processor.settings['range_min'])
        self.sb_range_max.setValue(self.processor.settings['range_max'])
        
        self.chk_intensity_filter.setChecked(self.processor.settings['intensity_filter_enabled'])
        self.sb_min_intensity.setValue(self.processor.settings['min_intensity'])
        
        mode = self.processor.settings['completion_mode']
        if mode == 'none': self.rb_comp_none.setChecked(True)
        elif mode == 'connected': self.rb_comp_conn.setChecked(True)
        elif mode == 'morphological': self.rb_comp_morph.setChecked(True)
        
        self.sb_hole_size.setValue(self.processor.settings['hole_size'])
        self.sb_morph_kernel.setValue(self.processor.settings['morph_kernel'])
        self.chk_apply_realtime.setChecked(self.processor.settings['enabled'])

        # Connect Signals
        self.chk_dbscan.toggled.connect(self.update_algo_settings)
        self.sb_dbscan_eps.valueChanged.connect(self.update_algo_settings)
        self.sb_dbscan_min_points.valueChanged.connect(self.update_algo_settings)
        
        self.chk_range_gate.toggled.connect(self.update_algo_settings)
        self.sb_range_min.valueChanged.connect(self.update_algo_settings)
        self.sb_range_max.valueChanged.connect(self.update_algo_settings)
        
        self.chk_intensity_filter.toggled.connect(self.update_algo_settings)
        self.sb_min_intensity.valueChanged.connect(self.update_algo_settings)
        
        self.rb_comp_none.toggled.connect(self.update_algo_settings)
        self.rb_comp_conn.toggled.connect(self.update_algo_settings)
        self.rb_comp_morph.toggled.connect(self.update_algo_settings)
        
        self.sb_hole_size.valueChanged.connect(self.update_algo_settings)
        self.sb_morph_kernel.valueChanged.connect(self.update_algo_settings)
        self.chk_apply_realtime.toggled.connect(self.update_algo_settings)

    def update_algo_settings(self):
        mode = 'none'
        if self.rb_comp_conn.isChecked(): mode = 'connected'
        elif self.rb_comp_morph.isChecked(): mode = 'morphological'
        
        settings = {
            'dbscan_enabled': self.chk_dbscan.isChecked(),
            'dbscan_eps': self.sb_dbscan_eps.value(),
            'dbscan_min_points': self.sb_dbscan_min_points.value(),
            
            'range_gate_enabled': self.chk_range_gate.isChecked(),
            'range_min': self.sb_range_min.value(),
            'range_max': self.sb_range_max.value(),
            
            'intensity_filter_enabled': self.chk_intensity_filter.isChecked(),
            'min_intensity': self.sb_min_intensity.value(),
            
            'completion_mode': mode,
            'hole_size': self.sb_hole_size.value(),
            'morph_kernel': self.sb_morph_kernel.value(),
            
            'enabled': self.chk_apply_realtime.isChecked()
        }
        self.processor.update_settings(settings)
        
        # If not streaming and not playing back, update the offline display immediately
        if not self.receiving and not self.playback.file_handle:
            self.update_offline_display()

    # pyqtgraph窗口初始化逻辑
    def setup_image_view(self, glw, sb_min, sb_max, cmap_name=None):
        # glw is GraphicsLayoutWidget
        vb = glw.addViewBox()
        vb.setAspectLocked(True)
        img = pg.ImageItem()
        vb.addItem(img)
        
        # Add FPS Text Item
        txt_fps = pg.TextItem(text="FPS: 0.0", color='w', anchor=(0, 0))
        txt_fps.setPos(0, 0) # Top-left of the image (0,0)
        vb.addItem(txt_fps)
        
        # Add Colorbar (HistogramLUTItem)
        hist = pg.HistogramLUTItem()
        hist.setImageItem(img)
        glw.addItem(hist)
        
        # Set Colormap (Managed by HistogramLUTItem now)
        if cmap_name:
            try:
                cmap_obj = get_colormap(cmap_name)
                hist.gradient.setColorMap(cmap_obj)
            except Exception as e:
                print(f"Error loading colormap {cmap_name}: {e}")
        
        glw.img_item = img # Attach to widget for easy access if needed, or just return it
        
        # Connect SpinBoxes to Image Levels
        # When SpinBox changes -> Update Image Levels (Histogram will update automatically)
        sb_min.valueChanged.connect(lambda v: img.setLevels([v, sb_max.value()]))
        sb_max.valueChanged.connect(lambda v: img.setLevels([sb_min.value(), v]))
        
        # When Histogram/Image changes -> Update SpinBoxes
        # Use a flag to prevent recursion if needed, but simple update might be fine
        def on_levels_changed(*args):
            # Get levels from image
            min_v, max_v = img.getLevels()
            # Block signals to prevent feedback loop
            sb_min.blockSignals(True)
            sb_max.blockSignals(True)
            sb_min.setValue(int(min_v))
            sb_max.setValue(int(max_v))
            sb_min.blockSignals(False)
            sb_max.blockSignals(False)
            
        hist.sigLevelsChanged.connect(on_levels_changed)
        
        # Apply initial levels from SpinBoxes
        img.setLevels([sb_min.value(), sb_max.value()])
        
        return img, txt_fps

    # 点击图像显示对应像素值的处理逻辑
    def on_image_click(self, event, img_item, label):
        if img_item.image is None:
            return

        # Map to item coordinates
        pos = img_item.mapFromScene(event.scenePos())
        x, y = int(pos.x()), int(pos.y())
        
        # Check bounds
        if 0 <= x < img_item.image.shape[0] and 0 <= y < img_item.image.shape[1]:
            val = img_item.image[x, y]
            self.lbl_pixel_info.setText(f"【{label}】\n坐标: ({x}, {y})\n数值: {val}")
        else:
            self.lbl_pixel_info.setText(f"【{label}】\n点击越界")

    # 网络连接按钮处理逻辑
    def toggle_connect(self):
        if not self.receiving:
            # Stop playback if active
            if self.playback.file_handle:
                self.playback.stop()
                self.lbl_play_status.setText("回放已停止")
            
            # Reset Play button for stream mode
            self.btn_play.setChecked(False)
            self.btn_play.setText("暂停推流")
            self.btn_play.setEnabled(True)

            ip = self.txt_ip.text()
            try:
                port = int(self.sb_port.value())
            except ValueError:
                port = 5005
            
            self.receiver = UdpReceiver(ip, port, 
                                        self.handle_int_rng, 
                                        self.handle_tof,
                                        self.recorder)
            self.receiver.start()
            self.receiving = True
            self.btn_conn.setText("断开")
        else:
            if self.receiver:
                self.receiver.stop()
                self.receiver.join(timeout=1.0)
                self.receiver = None
            self.receiving = False
            self.btn_conn.setText("连接")
            
            # Reset Play button for playback mode
            self.btn_play.setChecked(False)
            if self.playback.file_handle:
                self.btn_play.setText("播放")
                self.btn_play.setEnabled(True)
            else:
                self.btn_play.setText("播放/暂停")
                self.btn_play.setEnabled(False)

    def handle_int_rng(self, intensity, rng, task_id=None):
        self.sig_update_int_rng.emit(intensity, rng, task_id)

    def handle_tof(self, tof, task_id=None):
        self.sig_update_tof.emit(tof, task_id)

    # 录制按钮处理逻辑
    def load_playback_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择录制文件", "", "Binary Files (*.bin)")
        if filename:
            # Stop UDP stream if active
            if self.receiving:
                self.toggle_connect()

            if self.playback.load_file(filename):
                self.lbl_play_status.setText(f"已加载: {filename.split('/')[-1]}")
                self.btn_play.setEnabled(True)
                self.btn_play.setText("播放")
                self.btn_play.setChecked(False)
            else:
                self.lbl_play_status.setText("加载失败")

    # 推流/回放按钮处理逻辑（共用一个按钮）
    def toggle_playback_or_stream(self):
        # 1. UDP Stream Mode
        if self.receiving and self.receiver:
            if self.btn_play.isChecked(): # Paused
                self.receiver.paused = True
                self.btn_play.setText("恢复推流")
            else: # Resumed
                self.receiver.paused = False
                self.btn_play.setText("暂停推流")
        
        # 2. File Playback Mode
        elif not self.receiving and self.playback.file_handle:
            if self.btn_play.isChecked():
                self.playback.start()
                self.btn_play.setText("暂停")
            else:
                self.playback.pause()
                self.btn_play.setText("播放")

    # 回放功能的 UI 更新和完成处理逻辑
    def update_playback_ui(self, current, total):
        self.lbl_play_status.setText(f"进度: {current}/{total}")

    def on_playback_finished(self):
        self.btn_play.setChecked(False)
        self.btn_play.setText("播放")
        self.lbl_play_status.setText("播放结束")

    def update_img_levels(self, img, min_val, max_val):
        img.setLevels([min_val, max_val])

    # 保存录制文件按钮处理逻辑
    def select_recording_path(self):
        directory = QFileDialog.getExistingDirectory(self, "选择保存文件夹", "")
        if directory:
            self.txt_rec_path.setText(directory)

    # 录制按钮处理逻辑
    def toggle_record(self):
        if self.btn_rec.isChecked():
            # Get directory
            save_dir = self.txt_rec_path.text().strip()
            if not save_dir:
                save_dir = "." # Default to current dir if empty
            
            # Start recording (pass directory, file creation happens on first packet)
            if self.recorder.start_recording(save_dir):
                self.btn_rec.setText(f"停止录制")
                self.lbl_rec_status.setText(f"录制中 (等待数据)...")
                self.txt_rec_path.setEnabled(False)
                self.btn_set_rec_path.setEnabled(False)
            else:
                self.btn_rec.setChecked(False)
                self.lbl_rec_status.setText("录制失败")
        else:
            self.recorder.stop_recording()
            self.btn_rec.setText("开始录制")
            self.lbl_rec_status.setText("状态: 空闲")
            self.txt_rec_path.setEnabled(True)
            self.btn_set_rec_path.setEnabled(True)

    def update_status(self):
        status, bytes_written = self.recorder.get_status()
        if self.recorder.recording:
            mb = bytes_written / 1024 / 1024
            self.lbl_rec_status.setText(f"{status} ({mb:.1f} MB)")



    # 重建按钮逻辑
    def start_reconstruction(self):
        # 1. Stop any active stream/playback
        if self.receiving:
            self.toggle_connect()
        if self.playback.file_handle:
            self.playback.stop()
            self.btn_play.setChecked(False)
            self.btn_play.setText("播放")

        # 2. Get file
        filename, _ = QFileDialog.getOpenFileName(self, "选择 ToF 数据文件", "", "Binary Files (*.bin)")
        if not filename: return

        # 3. Get algorithm and parameters
        algo = "peak"
        params = {}
        
        # Determine Algorithm
        if self.rb_offline_peak.isChecked():
            algo = "peak"
        elif self.rb_offline_matched.isChecked():
            algo = "matched"
            params['pulse_width'] = self.sb_matched_width.value()
        elif self.rb_offline_derivative.isChecked():
            algo = "derivative"
            params['step'] = self.sb_deriv_step.value()
            params['threshold'] = self.sb_deriv_thresh.value()
        
        # 4. Get Spatial Correlation setting
        use_spatial = self.chk_spatial_corr.isChecked()
        if use_spatial:
            params['spatial_kernel'] = self.sb_spatial_kernel.value()

        # Get Frame Limit
        max_frames = self.sb_recon_frames.value()

        # 5. Start Thread
        self.reconstructor = Reconstructor(filename, algo, use_spatial, params, max_frames)
        
        self.reconstructor.sig_progress.connect(self.on_reconstruct_progress)
        self.reconstructor.sig_finished.connect(self.on_reconstruct_finished)
        self.reconstructor.sig_error.connect(self.on_reconstruct_error)
        self.reconstructor.sig_global_hist.connect(self.on_global_hist_update)
        
        self.btn_reconstruct.setEnabled(False)
        self.progress_reconstruct.setValue(0)
        self.reconstructor.run()

    # 全局直方图更新逻辑
    def on_global_hist_update(self, x_axis, counts):
        
        try:
            x_axis = np.asarray(x_axis)
            counts = np.asarray(counts)

            # pyqtgraph stepMode=True requires len(x) == len(y) + 1
            if x_axis.ndim == 1 and counts.ndim == 1:
                if len(x_axis) == len(counts):
                    if len(x_axis) > 0:
                        step = x_axis[-1] - x_axis[-2] if len(x_axis) > 1 else 1
                        x_axis = np.append(x_axis, x_axis[-1] + step)
                    else:
                        x_axis = np.array([0])
                elif len(x_axis) > len(counts) + 1:
                    x_axis = x_axis[:len(counts) + 1]
                elif len(x_axis) < len(counts) + 1:
                    counts = counts[:max(0, len(x_axis) - 1)]

            self.hist_tof.plot(x_axis, counts, stepMode=True, fillLevel=0, brush=(255, 100, 0, 150), clear=True)
            self.hist_tof.setTitle("ToF 全局光子分布直方图")
        except Exception as e:
            print(f"Error plotting global histogram: {e}")

    # 重建过程中的进度更新、错误处理和完成处理
    def on_reconstruct_progress(self, val):
        self.progress_reconstruct.setValue(val)

    def on_reconstruct_error(self, msg):
        self.btn_reconstruct.setEnabled(True)
        self.progress_reconstruct.setValue(0)
        self.lbl_pixel_info.setText(f"重建错误: {msg}")
        print(f"Reconstruction Error: {msg}")

    def on_reconstruct_finished(self, intensity, rng):
        self.btn_reconstruct.setEnabled(True)
        self.progress_reconstruct.setValue(100)
        
        # Store Raw Results
        self.raw_recon_int = intensity
        self.raw_recon_rng = rng
        
        self.lbl_pixel_info.setText("重建完成")
        
        # Trigger Display Update (Apply Processing if checked)
        self.update_offline_display()

    def update_offline_display(self):
        if self.raw_recon_int is None:
            return

        # Check if we should apply processing
        if self.chk_apply_realtime.isChecked():
            processed_int, processed_rng = self.processor.process(self.raw_recon_int, self.raw_recon_rng)
            self.img_int.setImage(processed_int.T, autoLevels=False)
            self.img_rng.setImage(processed_rng.T, autoLevels=False)
            self.lbl_pixel_info.setText("重建完成 (已应用后处理)")
        else:
            self.img_int.setImage(self.raw_recon_int.T, autoLevels=False)
            self.img_rng.setImage(self.raw_recon_rng.T, autoLevels=False)
            self.lbl_pixel_info.setText("重建完成 (原始数据)")

    # 后处理完成后的显示更新（实时流和重建后处理共用）
    def update_display_int_rng(self, intensity, rng, task_id=None):
        # Apply Post-Processing
        intensity, rng = self.processor.process(intensity, rng)

        # Update FPS
        curr_time = time.time()
        dt = curr_time - self.fps_int_last_time
        if dt > 0:
            fps = 1.0 / dt
            # Update both text items
            if task_id is not None:
                self.txt_fps_int.setText(f"FPS: {fps:.1f} | TID: {task_id}")
                self.txt_fps_rng.setText(f"FPS: {fps:.1f} | TID: {task_id}")
            else:
                self.txt_fps_int.setText(f"FPS: {fps:.1f}")
                self.txt_fps_rng.setText(f"FPS: {fps:.1f}")
        self.fps_int_last_time = curr_time

        self.img_int.setImage(intensity.T, autoLevels=False)
        self.img_rng.setImage(rng.T, autoLevels=False)
        
        try:
            ds_int = intensity[::4, ::4]
            y, x = np.histogram(ds_int, bins=50)
            self.hist_int.plot(x, y, stepMode=True, fillLevel=0, brush=(0,0,255,150), clear=True)
            
            ds_rng = rng[::4, ::4]
            y, x = np.histogram(ds_rng, bins=50)
            self.hist_rng.plot(x, y, stepMode=True, fillLevel=0, brush=(0,255,0,150), clear=True)
        except:
            pass
    
    # ToF 图像更新
    def update_display_tof(self, tof, task_id=None):
        # Update FPS
        curr_time = time.time()
        dt = curr_time - self.fps_tof_last_time
        if dt > 0:
            fps = 1.0 / dt
            if task_id is not None:
                self.txt_fps_tof.setText(f"FPS: {fps:.1f} | TID: {task_id}")
            else:
                self.txt_fps_tof.setText(f"FPS: {fps:.1f}")
        self.fps_tof_last_time = curr_time

        self.img_tof.setImage(tof.T, autoLevels=False)
        try:
            ds_tof = tof[::4, ::4]
            y, x = np.histogram(ds_tof, bins=50)
            self.hist_tof.plot(x, y, stepMode=True, fillLevel=0, brush=(255,0,0,150), clear=True)
        except:
            pass
    
    def handle_cmd(self, cmd_name, action_func=None):
        self.last_cmd_name = cmd_name
        if hasattr(self, 'lbl_recv_cmd_type'):
            self.lbl_recv_cmd_type.setText(cmd_name)
            self.lbl_recv_result.setStyleSheet("font-weight: bold; color: orange;")
            self.lbl_recv_result.setText("等待响应...")
        if action_func:
            action_func()

    # 串口通信相关的初始化和处理逻辑
    def init_serial_logic(self):
        # connect worker signals
        self.serial_worker.sig_received_frame.connect(self.on_serial_frame)
        self.serial_worker.sig_log.connect(self.log_serial)
        self.serial_worker.sig_status_update.connect(lambda msg: self.statusBar().showMessage(msg, 3000))
        
        # UI connections
        self.btn_serial_open.clicked.connect(self.toggle_serial)
        self.combo_port.installEventFilter(self)
        self.refresh_ports()
        
        # Commands
        self.btn_cmd_cooler_on.clicked.connect(lambda: self.handle_cmd("制冷机上电", lambda: self.serial_worker.set_cooler_on(True)))
        self.btn_cmd_cooler_off.clicked.connect(lambda: self.handle_cmd("制冷机下电", lambda: self.serial_worker.set_cooler_on(False)))
        self.btn_cmd_apd_on.clicked.connect(lambda: self.handle_cmd("探测器上电", lambda: self.serial_worker.set_apd_on(True)))
        self.btn_cmd_apd_off.clicked.connect(lambda: self.handle_cmd("探测器下电", lambda: self.serial_worker.set_apd_on(False)))
        self.btn_cmd_apd_config.clicked.connect(lambda: self.handle_cmd("APD配置", self.send_apd_config_cmd))
        
        # Temp 0xC3
        self.btn_cmd_set_temp.clicked.connect(lambda: self.handle_cmd("设置温度", self.send_temp_cmd))
        
        # Bias 0xCA
        self.btn_cmd_set_bias.clicked.connect(lambda: self.handle_cmd("设置偏压", self.send_bias_cmd))
        
        # Algo Config 0xC5
        if hasattr(self, 'btn_cmd_algo_config'):
             self.btn_cmd_algo_config.clicked.connect(lambda: self.handle_cmd("算法配置", self.send_algo_cmd))
        
        # Projectile 0xC6
        if hasattr(self, 'btn_cmd_proj_info'):
             self.btn_cmd_proj_info.clicked.connect(lambda: self.handle_cmd("弹体信息", self.send_proj_cmd))
             
        # Initialize Validators for Manual Inputs
        if hasattr(self, 'txt_set_temp'):
            # Temp: 223 - 253, 1 decimal place? (0.1K)
            val_temp = QDoubleValidator(223.0, 263.0, 1, self)
            val_temp.setNotation(QDoubleValidator.StandardNotation)
            self.txt_set_temp.setValidator(val_temp)
            self.txt_set_temp.setPlaceholderText("223-263")
            
        if hasattr(self, 'txt_set_bias'):
            # Bias: 10 - 63.5, 1 decimal place?
            val_bias = QDoubleValidator(10.0, 71, 1, self)
            val_bias.setNotation(QDoubleValidator.StandardNotation)
            self.txt_set_bias.setValidator(val_bias)
            self.txt_set_bias.setPlaceholderText("10-71")

        if hasattr(self, 'grp_serial_log'):
            if hasattr(self, 'btn_export_log'):
                self.btn_export_log.clicked.connect(self.export_serial_log)

    def export_serial_log(self):
        log_text = self.txt_serial_log.toPlainText()
        if not log_text:
            self.statusBar().showMessage("日志为空", 3000)
            return
            
        path, _ = QFileDialog.getSaveFileName(self, "导出串口日志", "serial_log.txt", "Text Files (*.txt);;All Files (*)")
        
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(log_text)
                self.statusBar().showMessage(f"日志已导出至: {path}", 3000)
            except Exception as e:
                self.statusBar().showMessage(f"日志导出失败: {e}", 3000)

    def eventFilter(self, obj, event):
        if obj == self.combo_port and event.type() == QEvent.MouseButtonPress:
            self.refresh_ports()
        return super().eventFilter(obj, event)

    def refresh_ports(self):
        current_port = self.combo_port.currentData()
        self.combo_port.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            desc = f"{p.device} - {p.description}" if p.description else p.device
            self.combo_port.addItem(desc, userData=p.device)
        
        if current_port:
            idx = self.combo_port.findData(current_port)
            if idx >= 0:
                self.combo_port.setCurrentIndex(idx)
        
    # 串口打开逻辑
    def toggle_serial(self):
        if self.serial_worker.running:
            self.serial_worker.close_port()
            self.btn_serial_open.setText("打开串口")
            self.btn_serial_open.setChecked(False)
        else:
            port = self.combo_port.currentData()
            if not port:
                return
            
            try:
                baud = int(self.combo_baud.currentText())
            except ValueError:
                baud = 115200 # Default fallback
            
            if self.serial_worker.open_port(port, baud):
                self.btn_serial_open.setText("关闭串口")
                self.btn_serial_open.setChecked(True)
            else:
                self.btn_serial_open.setChecked(False)

    def send_temp_cmd(self):
        txt = self.txt_set_temp.text().strip()
        try:
            temp_k = float(txt)
        except ValueError:
            self.statusBar().showMessage("Error: Temperature must be a number", 3000)
            return

        if not (223 <= temp_k <= 263):
            self.statusBar().showMessage("Error: Temperature must be between 223K and 263K", 3000)
            return

        val = int(temp_k)
        self.serial_worker.protocol.set_temp(val)
        self.statusBar().showMessage(f"Sent Temp: {temp_k}K", 3000)

    def send_bias_cmd(self):
        txt = self.txt_set_bias.text().strip()
        try:
            val = float(txt)
        except ValueError:
            self.statusBar().showMessage("Error: Voltage must be a number", 3000)
            return

        if not (10 <= val <= 71):
            self.statusBar().showMessage("Error: Voltage must be between 10V and 71V", 3000)
            return

        v_int = int(val)
        v_dec = int(round((val - v_int) * 10))
        self.serial_worker.protocol.set_bias(v_int, v_dec)
        self.statusBar().showMessage(f"Sent Bias: {val}V", 3000)

    def send_algo_cmd(self):
        f = self.sb_algo_frames.value() & 0x0F
        n = self.sb_algo_noise.value() & 0x0F
        s = self.sb_algo_step.value() & 0x0F
        t = self.sb_algo_thresh.value() & 0x0F
        k = self.sb_algo_kernel.value() & 0xFF
        
        self.serial_worker.protocol.set_algo(f, n, s, t, k)
        self.statusBar().showMessage("Sent Algo Config", 3000)

    def send_proj_cmd(self):
        dist = int(self.sb_proj_dist.value())
        vel = int(self.sb_proj_vel.value())
        
        self.serial_worker.protocol.set_proj_info(dist, vel)
        self.statusBar().showMessage(f"Sent Projectile Info: {dist}m, {vel}m/s", 3000)

    def send_apd_config_cmd(self):
        trig = self.chk_apd_trig.isChecked()
        test_pt = self.chk_apd_test_point.isChecked()
        test_mode = self.chk_apd_test_mode.isChecked()
        self.serial_worker.protocol.set_apd_config(trig, test_pt, test_mode)
        self.statusBar().showMessage("Sent APD Config", 3000)

    def log_serial(self, msg):
        self.txt_serial_log.append(msg)
        # Auto scroll
        sb = self.txt_serial_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_serial_frame(self, data):
        # Parse data dict
        version = data.get('version', '')
        temp = data.get('temp', 0)
        volt = data.get('volt', 0)

        # Build status string for failures
        failures = []
        if data.get('test_status') == 1: failures.append("Test")
        if data.get('apd_bias_status') == 1: failures.append("Bias")
        if data.get('apd_ctrl_status') == 1: failures.append("APD_Ctrl")
        if data.get('algo_status') == 1: failures.append("Algo")

        # APD power: byte 12 (新逻辑: 1=成功, 0=失败)
        power_st = data.get('power_status', 0)
        # 低位: 制冷机, 高位: APD
        if (power_st & 0x01) == 0: failures.append("Cooler 上电")
        if (power_st & 0x02) == 0: failures.append("APD 上电")

        status_msg = "Fail: " + ", ".join(failures) if failures else "All OK"

        if hasattr(self, 'lbl_recv_cmd_type'):
            self.lbl_recv_cmd_type.setText(getattr(self, 'last_cmd_name', '--'))

            if failures:
                res_str = "Fail"
                self.lbl_recv_result.setStyleSheet("font-weight: bold; color: red;")
            else:
                res_str = "Success"
                self.lbl_recv_result.setStyleSheet("font-weight: bold; color: green;")

            self.lbl_recv_result.setText(res_str)

            if hasattr(self, 'lbl_recv_temp'):
                self.lbl_recv_temp.setText(f"{temp} K")
            if hasattr(self, 'lbl_recv_volt'):
                self.lbl_recv_volt.setText(f"{volt:.1f} V")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    apply_dark_theme(app)
    
    pg.setConfigOption('background', '#1e1e1e')

    pg.setConfigOption('background', 'k')
    pg.setConfigOption('foreground', '#dcdcdc') 
    
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())
