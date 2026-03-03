import sys
import os
import numpy as np
import threading
import time
import socket
from collections import defaultdict
import struct

from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QSpinBox, QPushButton, QCheckBox, QGroupBox, QTabWidget, QSplitter, QFileDialog
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer
from PyQt5 import uic
import pyqtgraph as pg

from core.parser import DataParser
from core.receiver import UdpReceiver
from core.recorder import DataRecorder
from core.playback import PlaybackManager
from core.reconstructor import Reconstructor
from utils.theme import apply_dark_theme
import config

# =============================================================================
# Main Window
# =============================================================================
class MainWindow(QMainWindow):
    sig_update_int_rng = pyqtSignal(object, object)
    sig_update_tof = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("单光子激光雷达上位机 v3.0")
        # self.resize(2000, 900)
        
        self.receiver = None
        self.recorder = DataRecorder()
        self.playback = PlaybackManager()
        self.recorder.start() # Start recorder thread
        self.receiving = False
        
        self.init_ui()
        
        # Connect signals
        self.sig_update_int_rng.connect(self.update_display_int_rng)
        self.sig_update_tof.connect(self.update_display_tof)
        
        # Playback Signals
        self.playback.sig_update_int_rng.connect(self.update_display_int_rng)
        self.playback.sig_update_tof.connect(self.update_display_tof)
        self.playback.sig_progress.connect(self.update_playback_ui)
        self.playback.sig_finished.connect(self.on_playback_finished)
        
        # Timer for updating status
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(1000)

    def closeEvent(self, event):
        if self.receiver:
            self.receiver.stop()
        if self.recorder:
            self.recorder.close()
        if self.playback:
            self.playback.close()
        event.accept()

    def init_ui(self):
        # Load UI from file
        try:
            uic.loadUi("ui/mainwindow.ui", self)
        except Exception as e:
            print(f"Error loading UI: {e}")
            return

        # Setup Splitter Sizes (70% - 30%)
        self.splitter.setSizes([896, 384])
        
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

    def setup_image_view(self, glw, sb_min, sb_max, cmap_name=None):
        # glw is GraphicsLayoutWidget
        vb = glw.addViewBox()
        vb.setAspectLocked(True)
        img = pg.ImageItem()
        vb.addItem(img)
        
        # Set Colormap
        if cmap_name:
            # pg.colormap.get returns a ColorMap object
            # We can use it to get a lookup table
            cmap = pg.colormap.get(cmap_name)
            img.setLookupTable(cmap.getLookupTable())

        # Add FPS Text Item
        txt_fps = pg.TextItem(text="FPS: 0.0", color='w', anchor=(0, 0))
        txt_fps.setPos(0, 0) # Top-left of the image (0,0)
        vb.addItem(txt_fps)
        
        glw.img_item = img # Attach to widget for easy access if needed, or just return it
        
        # Connect SpinBoxes
        sb_min.valueChanged.connect(lambda v: self.update_img_levels(img, v, sb_max.value()))
        sb_max.valueChanged.connect(lambda v: self.update_img_levels(img, sb_min.value(), v))
        
        # Apply initial levels immediately
        self.update_img_levels(img, sb_min.value(), sb_max.value())
        
        return img, txt_fps

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

    def load_playback_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择录制文件", "", "Binary Files (*.bin)")
        if filename:
            if self.playback.load_file(filename):
                self.lbl_play_status.setText(f"已加载: {filename.split('/')[-1]}")
                self.btn_play.setEnabled(True)
                self.btn_play.setText("播放")
                self.btn_play.setChecked(False)
            else:
                self.lbl_play_status.setText("加载失败")

    def toggle_playback(self):
        if self.btn_play.isChecked():
            self.playback.start()
            self.btn_play.setText("暂停")
        else:
            self.playback.pause()
            self.btn_play.setText("播放")

    def update_playback_ui(self, current, total):
        self.lbl_play_status.setText(f"进度: {current}/{total}")

    def on_playback_finished(self):
        self.btn_play.setChecked(False)
        self.btn_play.setText("播放")
        self.lbl_play_status.setText("播放结束")

    def update_img_levels(self, img, min_val, max_val):
        img.setLevels([min_val, max_val])

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

    def update_playback_ui(self, current, total):
        self.lbl_play_status.setText(f"进度: {current}/{total}")

    def on_playback_finished(self):
        self.btn_play.setChecked(False)
        self.btn_play.setText("播放")
        self.lbl_play_status.setText("播放结束")

    def update_img_levels(self, img, min_val, max_val):
        img.setLevels([min_val, max_val])

    def select_recording_path(self):
        directory = QFileDialog.getExistingDirectory(self, "选择保存文件夹", "")
        if directory:
            self.txt_rec_path.setText(directory)

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

    def handle_int_rng(self, intensity, rng):
        self.sig_update_int_rng.emit(intensity, rng)

    def handle_tof(self, tof):
        self.sig_update_tof.emit(tof)

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

        # 3. Get algorithm
        algo_idx = self.cmb_algo.currentIndex()
        algo = "peak" if algo_idx == 0 else "matched"
        
        # 4. Get Spatial Correlation setting
        use_spatial = self.chk_spatial_corr.isChecked()

        # 5. Start Thread
        self.reconstructor = Reconstructor(filename, algo, use_spatial)
        self.reconstructor.sig_progress.connect(self.on_reconstruct_progress)
        self.reconstructor.sig_finished.connect(self.on_reconstruct_finished)
        self.reconstructor.sig_error.connect(self.on_reconstruct_error)
        
        self.btn_reconstruct.setEnabled(False)
        self.progress_reconstruct.setValue(0)
        self.reconstructor.start()

    def on_reconstruct_progress(self, val):
        self.progress_reconstruct.setValue(val)

    def on_reconstruct_finished(self, intensity, rng):
        self.btn_reconstruct.setEnabled(True)
        self.progress_reconstruct.setValue(100)
        
        # Display Result in the Intensity/Range Tabs
        self.img_int.setImage(intensity.T, autoLevels=False)
        self.img_rng.setImage(rng.T, autoLevels=False)
        
        self.lbl_pixel_info.setText("重建完成")
        # Switch to Display Tab automatically? Maybe not, keep focus.
        
    def on_reconstruct_error(self, msg):
        self.btn_reconstruct.setEnabled(True)
        self.lbl_pixel_info.setText(f"重建错误: {msg}")
        print(f"Reconstruction Error: {msg}")

    @pyqtSlot(object, object)
    def update_display_int_rng(self, intensity, rng):
        # Update FPS
        curr_time = time.time()
        dt = curr_time - self.fps_int_last_time
        if dt > 0:
            fps = 1.0 / dt
            # Update both text items
            self.txt_fps_int.setText(f"FPS: {fps:.1f}")
            self.txt_fps_rng.setText(f"FPS: {fps:.1f}")
        self.fps_int_last_time = curr_time

        self.img_int.setImage(intensity.T, autoLevels=False)
        self.img_rng.setImage(rng.T, autoLevels=False)
        
        try:
            # Downsample for faster histogram
            ds_int = intensity[::4, ::4]
            y, x = np.histogram(ds_int, bins=50)
            self.hist_int.plot(x, y, stepMode=True, fillLevel=0, brush=(0,0,255,150), clear=True)
            
            ds_rng = rng[::4, ::4]
            y, x = np.histogram(ds_rng, bins=50)
            self.hist_rng.plot(x, y, stepMode=True, fillLevel=0, brush=(0,255,0,150), clear=True)
        except:
            pass

    @pyqtSlot(object)
    def update_display_tof(self, tof):
        # Update FPS
        curr_time = time.time()
        dt = curr_time - self.fps_tof_last_time
        if dt > 0:
            fps = 1.0 / dt
            self.txt_fps_tof.setText(f"FPS: {fps:.1f}")
        self.fps_tof_last_time = curr_time

        self.img_tof.setImage(tof.T, autoLevels=False)
        try:
            ds_tof = tof[::4, ::4]
            y, x = np.histogram(ds_tof, bins=50)
            self.hist_tof.plot(x, y, stepMode=True, fillLevel=0, brush=(255,0,0,150), clear=True)
        except:
            pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Apply Linux-style Dark Theme
    apply_dark_theme(app)
    
    # PyQtGraph Global Config for consistency with Dark Theme
    pg.setConfigOption('background', '#1e1e1e') # Darker than default 'k' (black) to match input fields? Or stick to black? 
    # Black is best for scientific data usually. Let's use standard hex for consistent dark grey if desired, 
    # but black (k) is standard for plots.
    # Let's stick to 'k' but make foreground slightly off-white.
    pg.setConfigOption('background', 'k')
    pg.setConfigOption('foreground', '#dcdcdc') # Matches our theme text color
    
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())
