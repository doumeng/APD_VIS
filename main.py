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
from core.processor import ImageProcessor
from utils.theme import apply_dark_theme
from utils.colormaps import get_colormap
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
        
        # --- Setup Reconstruction UI (Dynamic Layout) ---
        self.setup_reconstruction_ui()

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

    def setup_reconstruction_ui(self):
        # Organize Reconstruction Tab into 3 Groups
        # Find the parent widget of reconstruction controls (likely a Tab or Widget)
        # We assume they are in a layout. We will reparent them.
        
        parent_widget = self.btn_reconstruct.parent()
        layout = parent_widget.layout()
        
        # If no layout, create one
        if layout is None:
            layout = QVBoxLayout(parent_widget)
        
        # Clear existing layout (but keep widgets alive)
        # We can't easily clear layout without deleting widgets.
        # Instead, we will create new GroupBoxes and move widgets into them.
        
        # 1. Data Settings Group
        gb_data = QGroupBox("数据设置")
        layout_data = QVBoxLayout()
        gb_data.setLayout(layout_data)
        
        # Add Frame Count Input
        lbl_frames = QLabel("重建帧数 (0=全部):")
        self.sb_recon_frames = QSpinBox()
        self.sb_recon_frames.setRange(0, 100000)
        self.sb_recon_frames.setValue(0)
        
        layout_data.addWidget(lbl_frames)
        layout_data.addWidget(self.sb_recon_frames)
        
        # 2. Spatial Filter Group
        gb_spatial = QGroupBox("空间滤波")
        layout_spatial = QVBoxLayout()
        gb_spatial.setLayout(layout_spatial)
        
        # Move existing widgets
        # We remove them from their old layout first if possible, or just add them to new layout (automatically reparents)
        layout_spatial.addWidget(self.chk_spatial_corr)
        layout_spatial.addWidget(QLabel("邻域大小:"))
        layout_spatial.addWidget(self.sb_spatial_kernel)
        
        # 3. Algorithm Config Group
        gb_algo = QGroupBox("重建算法配置")
        layout_algo = QVBoxLayout()
        gb_algo.setLayout(layout_algo)
        
        layout_algo.addWidget(self.rb_offline_peak)
        layout_algo.addWidget(self.rb_offline_matched)
        
        # Horizontal layout for Matched Filter params
        h_matched = QHBoxLayout()
        h_matched.addWidget(QLabel("脉宽:"))
        h_matched.addWidget(self.sb_matched_width)
        layout_algo.addLayout(h_matched)
        
        layout_algo.addWidget(self.rb_offline_derivative)
        
        # Horizontal layout for Derivative params
        h_deriv = QHBoxLayout()
        h_deriv.addWidget(QLabel("步长:"))
        h_deriv.addWidget(self.sb_deriv_step)
        h_deriv.addWidget(QLabel("阈值:"))
        h_deriv.addWidget(self.sb_deriv_thresh)
        layout_algo.addLayout(h_deriv)
        
        # 4. Rebuild Main Layout
        # We need to insert these groups into the parent layout
        # Since we can't easily replace the exact position in a loaded UI, 
        # we might append them or try to replace the content of the tab.
        
        # Simplest approach: Create a new layout for the tab, add groups, add button.
        # But we need to remove old items first to avoid duplication/mess.
        
        # Let's try to identify the layout items and remove them.
        # Or just hide the old container if it exists?
        
        # Alternative: The existing UI likely has a Vertical Layout.
        # We can just add our new Groups to it? No, duplicates.
        
        # Correct approach:
        # Reparent the widgets. The old layout will lose them.
        # Then add groups to the layout.
        
        # Let's clean the layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w and w not in [self.btn_reconstruct, self.chk_spatial_corr, self.sb_spatial_kernel, 
                               self.rb_offline_peak, self.rb_offline_matched, self.sb_matched_width,
                               self.rb_offline_derivative, self.sb_deriv_step, self.sb_deriv_thresh,
                               self.progress_reconstruct]:
                w.deleteLater() # Delete labels/spacers we don't track
        
        # Add Groups
        layout.addWidget(gb_data)
        layout.addWidget(gb_spatial)
        layout.addWidget(gb_algo)
        
        # Add Reconstruct Button at the bottom
        layout.addWidget(self.btn_reconstruct)
        layout.addWidget(self.progress_reconstruct)
        layout.addStretch()


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

    def on_global_hist_update(self, x_axis, counts):
        # Plot Global Histogram on ToF Histogram Widget
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

    def on_reconstruct_progress(self, val):
        self.progress_reconstruct.setValue(val)

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

        
    def on_reconstruct_error(self, msg):
        self.btn_reconstruct.setEnabled(True)
        self.progress_reconstruct.setValue(0)
        self.lbl_pixel_info.setText(f"重建错误: {msg}")
        print(f"Reconstruction Error: {msg}")

    @pyqtSlot(object, object)
    def update_display_int_rng(self, intensity, rng):
        # Apply Post-Processing
        intensity, rng = self.processor.process(intensity, rng)

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
