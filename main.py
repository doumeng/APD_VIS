'''
Author: doumeng1026@gmail.com
Date: 2026-04-27 10:28:04
LastEditors: Do not edit
LastEditTime: 2026-05-14 11:02:11
Description:
FilePath: \v3\main.py
'''
import sys
import os
import numpy as np
import threading
import time
import socket
from collections import defaultdict
import struct

from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QSpinBox, QPushButton, QCheckBox, QGroupBox, QTabWidget, QSplitter, QFileDialog, QDialog, QFormLayout, QDialogButtonBox, QDoubleSpinBox, QComboBox, QScrollArea
from PyQt5.QtGui import QDoubleValidator, QIcon, QImage
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer, QEvent
from PyQt5 import uic
import pyqtgraph as pg

try:
    import open3d as o3d
    HAS_OPEN3D = True
except ImportError:
    HAS_OPEN3D = False

import serial.tools.list_ports
from core.parser import DataParser
from core.receiver import UdpReceiver
from core.recorder import DataRecorder
from core.playback import PlaybackManager
from core.reconstructor import Reconstructor
from core.processor import ImageProcessor
from core.serial_protocol import SerialWorker
from core.scene_reconstruction import export_scene_point_cloud
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
    sig_update_int_rng = pyqtSignal(object, object, object, float, float)
    sig_update_tof = pyqtSignal(object, object, float, float)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("单光子激光雷达上位机 v3.0")
        self.setWindowIcon(QIcon(resource_path("icon.webp")))
        # self.resize(2000, 900)

        self.receiver = None
        self.recorder = DataRecorder()
        self.playback = PlaybackManager()
        self.processor = ImageProcessor()
        self._frame_lock = threading.Lock()
        self._latest_int_rng = None
        self._latest_tof = None
        self._rx_int_frames = 0
        self._rx_tof_frames = 0
        self._ui_int_frames = 0
        self._ui_tof_frames = 0
        self._diag_last_ts = time.time()
        self._diag_last_rx_int = 0
        self._diag_last_rx_tof = 0
        self._diag_last_ui_int = 0
        self._diag_last_ui_tof = 0
        self._bias_diag = {
            'target': 0.0,
            'send_time': 0.0,
            'first_ack': 0.0,
            'done_time': 0.0,
        }
        self._updating_playback_slider = False
        self._scene_recon_params = None

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

        self.flush_timer = QTimer()
        self.flush_timer.timeout.connect(self.flush_latest_frames)
        self.flush_timer.setTimerType(Qt.PreciseTimer)
        self.flush_timer.start(int(getattr(config, 'DISPLAY_FLUSH_INTERVAL_MS', 33)))

        self.diag_timer = QTimer()
        self.diag_timer.timeout.connect(self.update_runtime_diag)
        self.diag_timer.start(max(200, int(float(getattr(config, 'DIAG_UPDATE_INTERVAL_SEC', 1.0)) * 1000)))

        self.lbl_runtime_diag = QLabel("运行诊断: --")
        self.statusBar().addPermanentWidget(self.lbl_runtime_diag)

        # Initialize Serial Logic
        self.last_cmd_name = "--"
        self._serial_log_stream_path = ""
        self.serial_worker = SerialWorker()
        self.init_serial_logic()

    def closeEvent(self, event):
        if hasattr(self, 'flush_timer'):
            self.flush_timer.stop()
        if hasattr(self, 'diag_timer'):
            self.diag_timer.stop()
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
        self.last_hist_update_int_rng = 0.0
        self.last_hist_update_tof = 0.0

        # Access Widgets directly (they are now members of self)
        self.init_data_format_controls()
        self.init_playback_slider()

        # --- Setup Graphics (PyQtGraph) ---
        show_center_cross = self.chk_center_cross.isChecked() if hasattr(self, 'chk_center_cross') else True

        # 1. Intensity Image
        self.img_int, self.txt_fps_int, self.txt_servo_int, self.cross_int = self.setup_image_view(
            self.glw_int,
            self.sb_int_min,
            self.sb_int_max,
            config.DEFAULT_INTENSITY_CMAP,
            show_center_cross=show_center_cross,
        )
        self.vb_int = self.glw_int.view_box

        # 2. Range Image
        self.img_rng, self.txt_fps_rng, self.txt_servo_rng, self.cross_rng = self.setup_image_view(
            self.glw_rng,
            self.sb_rng_min,
            self.sb_rng_max,
            config.DEFAULT_RANGE_CMAP,
            show_center_cross=show_center_cross,
        )
        self.vb_rng = self.glw_rng.view_box

        # 3. ToF Image
        self.img_tof, self.txt_fps_tof, self.txt_servo_tof, _ = self.setup_image_view(
            self.glw_tof,
            self.sb_tof_min,
            self.sb_tof_max,
            config.DEFAULT_TOF_CMAP,
            show_center_cross=False,
        )

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
        if hasattr(self, 'btn_export_images'):
            self.btn_export_images.clicked.connect(self.export_current_images)

        # Playback
        self.btn_load.clicked.connect(self.load_playback_file)
        self.btn_play.clicked.connect(self.toggle_playback_or_stream)
        if hasattr(self, 'btn_scene_recon'):
            self.btn_scene_recon.clicked.connect(self.start_scene_reconstruction_from_playback)
            self.btn_scene_recon.setEnabled(False)

        # Reconstruction
        self.btn_reconstruct.clicked.connect(self.start_reconstruction)
        if hasattr(self, 'chk_center_cross'):
            self.chk_center_cross.toggled.connect(self.on_center_cross_toggled)

        # Mouse Clicks
        try:
            self.img_int.scene().sigMouseClicked.connect(lambda evt: self.on_image_click(evt, self.img_int, "Intensity"))
            self.img_rng.scene().sigMouseClicked.connect(lambda evt: self.on_image_click(evt, self.img_rng, "Range"))
            self.img_tof.scene().sigMouseClicked.connect(lambda evt: self.on_image_click(evt, self.img_tof, "ToF"))
        except AttributeError:
            print("Warning: Could not connect mouse click events (scene not ready?)")

        self._update_scene_recon_button_state()

    def init_data_format_controls(self):
        if not hasattr(self, 'combo_data_format'):
            return

        self.combo_data_format.blockSignals(True)
        self.combo_data_format.clear()
        self.combo_data_format.addItem("预处理数据", config.DATA_FORMAT_PREPROCESS)
        self.combo_data_format.addItem("信息处理板数据", config.DATA_FORMAT_INFO_BOARD)
        default_idx = self.combo_data_format.findData(getattr(config, 'DEFAULT_DATA_FORMAT', config.DATA_FORMAT_INFO_BOARD))
        self.combo_data_format.setCurrentIndex(default_idx if default_idx >= 0 else 1)
        self.combo_data_format.blockSignals(False)
        self.playback.set_data_format(self.current_data_format())
        self.combo_data_format.currentIndexChanged.connect(self.on_data_format_changed)

    def init_playback_slider(self):
        if hasattr(self, 'slider_playback_frame'):
            self.slider_playback_frame.setRange(0, 0)
            self.slider_playback_frame.setValue(0)
            self.slider_playback_frame.setEnabled(False)
            self.slider_playback_frame.sliderReleased.connect(self.on_playback_slider_released)
            self.slider_playback_frame.valueChanged.connect(self.on_playback_slider_value_changed)
        if hasattr(self, 'lbl_playback_frame'):
            self.lbl_playback_frame.setText("0/0")

    def current_data_format(self):
        if hasattr(self, 'combo_data_format'):
            data_format = self.combo_data_format.currentData()
            if data_format:
                return data_format
        return getattr(config, 'DEFAULT_DATA_FORMAT', config.DATA_FORMAT_INFO_BOARD)

    def on_data_format_changed(self, *_args):
        data_format = self.current_data_format()
        self.playback.set_data_format(data_format)
        if self.receiver:
            self.receiver.set_data_format(data_format)
        if self.playback.file_handle:
            self.statusBar().showMessage("数据格式已更新，后续回放/导出/重建将使用当前格式", 3000)

    def _set_playback_slider_position(self, current, total):
        if not hasattr(self, 'slider_playback_frame'):
            return

        total = max(0, int(total))
        current = max(0, int(current))
        if total <= 0:
            displayed_idx = 0
            display_text = "0/0"
        else:
            displayed_idx = max(0, min(current - 1 if current > 0 else 0, total - 1))
            display_text = f"{displayed_idx + 1}/{total}"

        self._updating_playback_slider = True
        try:
            self.slider_playback_frame.blockSignals(True)
            self.slider_playback_frame.setRange(0, max(0, total - 1))
            self.slider_playback_frame.setValue(displayed_idx)
            self.slider_playback_frame.blockSignals(False)
        finally:
            self._updating_playback_slider = False

        if hasattr(self, 'lbl_playback_frame'):
            self.lbl_playback_frame.setText(display_text)
        self._update_playback_slider_enabled()

    def _update_playback_slider_enabled(self):
        if not hasattr(self, 'slider_playback_frame'):
            return
        enabled = bool((not self.receiving) and self.playback.file_handle and self.playback.total_frames > 0)
        self.slider_playback_frame.setEnabled(enabled)

    def _seek_playback_from_slider(self):
        if self._updating_playback_slider:
            return
        if self.receiving or not self.playback.file_handle:
            return
        frame_idx = int(self.slider_playback_frame.value())
        self.playback.seek(frame_idx, emit_frame=True)

    def on_playback_slider_released(self):
        self._seek_playback_from_slider()

    def on_playback_slider_value_changed(self, _value):
        if not hasattr(self, 'slider_playback_frame'):
            return
        if not self.slider_playback_frame.isSliderDown():
            self._seek_playback_from_slider()

    def _is_loaded_depth_playback(self):
        return bool(self.playback.file_handle and self.playback.data_type == 0 and self.playback.filename)

    def _update_scene_recon_button_state(self):
        if hasattr(self, 'btn_scene_recon'):
            self.btn_scene_recon.setEnabled(self._is_loaded_depth_playback() and not self.receiving)
        if hasattr(self, 'progress_scene_recon'):
            self.progress_scene_recon.setValue(0)
        self._update_playback_slider_enabled()

    def _set_scene_recon_progress(self, current, total):
        if not hasattr(self, 'progress_scene_recon'):
            return
        total = max(1, int(total))
        current = max(0, min(int(current), total))
        percent = int(current * 100 / total)
        self.progress_scene_recon.setValue(percent)
        self.progress_scene_recon.setFormat(f"场景重建 {percent}% ({current}/{total})")
        QApplication.processEvents()

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
    def setup_image_view(self, glw, sb_min, sb_max, cmap_name=None, show_center_cross=False):
        # glw is GraphicsLayoutWidget
        vb = glw.addViewBox()
        glw.view_box = vb
        vb.setAspectLocked(True)
        vb.invertY(True)  # 将Y轴反转，使坐标原点在左上角
        img = pg.ImageItem()
        vb.addItem(img)

        center_cross = None
        if show_center_cross:
            center_cross = self._create_center_cross(vb)

        # Add FPS Text Item
        txt_fps = pg.TextItem(text="FPS: 0.0", color='w', anchor=(0, 0))
        txt_fps.setPos(0, 0) # Top-left of the image (0,0)
        vb.addItem(txt_fps)

        # Add Servo Text Item
        txt_servo = pg.TextItem(text="Pitch: --, Yaw: --", color='y', anchor=(0, 0))
        txt_servo.setPos(0, 15)
        txt_servo.setZValue(10)
        vb.addItem(txt_servo)

        # Add Colorbar (HistogramLUTItem)
        hist = pg.HistogramLUTItem()
        hist.setImageItem(img)
        glw.addItem(hist)
        glw.hist_lut_item = hist

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

        return img, txt_fps, txt_servo, center_cross

    def _create_center_cross(self, view_box, arm_len=5):
        h_line = pg.PlotCurveItem(pen=pg.mkPen('w', width=1))
        v_line = pg.PlotCurveItem(pen=pg.mkPen('w', width=1))
        h_line.setZValue(8)
        v_line.setZValue(8)
        view_box.addItem(h_line)
        view_box.addItem(v_line)

        self._set_cross_geometry(
            h_line,
            v_line,
            config.IMG_WIDTH // 2,
            config.IMG_HEIGHT // 2,
            arm_len,
            config.IMG_WIDTH,
            config.IMG_HEIGHT,
        )
        return h_line, v_line

    def _set_cross_geometry(self, h_line, v_line, cx, cy, arm_len, width, height):
        if width <= 0 or height <= 0:
            return
        x0 = max(0, cx - arm_len)
        x1 = min(width - 1, cx + arm_len)
        y0 = max(0, cy - arm_len)
        y1 = min(height - 1, cy + arm_len)

        h_line.setData([x0, x1], [cy, cy])
        v_line.setData([cx, cx], [y0, y1])

    def _update_center_cross(self, img_item, center_cross, arm_len=5):
        if center_cross is None or img_item.image is None:
            return

        height, width = img_item.image.shape[:2]
        cx = width // 2
        cy = height // 2
        h_line, v_line = center_cross
        self._set_cross_geometry(h_line, v_line, cx, cy, arm_len, width, height)

    def _remove_center_cross(self, view_box, center_cross):
        if center_cross is None:
            return None

        h_line, v_line = center_cross
        try:
            view_box.removeItem(h_line)
        except Exception:
            pass
        try:
            view_box.removeItem(v_line)
        except Exception:
            pass
        return None

    def on_center_cross_toggled(self, checked):
        if checked:
            if self.cross_int is None:
                self.cross_int = self._create_center_cross(self.vb_int)
            if self.cross_rng is None:
                self.cross_rng = self._create_center_cross(self.vb_rng)
            self._update_center_cross(self.img_int, self.cross_int)
            self._update_center_cross(self.img_rng, self.cross_rng)
        else:
            self.cross_int = self._remove_center_cross(self.vb_int, self.cross_int)
            self.cross_rng = self._remove_center_cross(self.vb_rng, self.cross_rng)

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
                self._set_playback_slider_position(0, self.playback.total_frames)

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
                                        self.recorder,
                                        self.current_data_format())
            self.receiver.start()
            self.receiving = True
            self.btn_conn.setText("断开")
            self._update_playback_slider_enabled()
            self._update_scene_recon_button_state()
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
            self._update_playback_slider_enabled()
            self._update_scene_recon_button_state()

    def handle_int_rng(self, intensity, rng, task_id=None, pitch=0.0, yaw=0.0):
        if self.receiving:
            with self._frame_lock:
                self._latest_int_rng = (intensity, rng, task_id, pitch, yaw)
                self._rx_int_frames += 1
            return
        self.sig_update_int_rng.emit(intensity, rng, task_id, pitch, yaw)

    def handle_tof(self, tof, task_id=None, pitch=0.0, yaw=0.0):
        if self.receiving:
            with self._frame_lock:
                self._latest_tof = (tof, task_id, pitch, yaw)
                self._rx_tof_frames += 1
            return
        self.sig_update_tof.emit(tof, task_id, pitch, yaw)

    def flush_latest_frames(self):
        int_item = None
        tof_item = None
        with self._frame_lock:
            if self._latest_int_rng is not None:
                int_item = self._latest_int_rng
                self._latest_int_rng = None
            if self._latest_tof is not None:
                tof_item = self._latest_tof
                self._latest_tof = None

        if int_item is not None:
            self._ui_int_frames += 1
            self.update_display_int_rng(*int_item)
        if tof_item is not None:
            self._ui_tof_frames += 1
            self.update_display_tof(*tof_item)

    # 录制按钮处理逻辑
    def load_playback_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择录制文件", "", "Binary Files (*.bin)")
        if filename:
            # Stop UDP stream if active
            if self.receiving:
                self.toggle_connect()

            self.playback.set_data_format(self.current_data_format())
            if self.playback.load_file(filename):
                self.lbl_play_status.setText(f"已加载: {os.path.basename(filename)}")
                self.btn_play.setEnabled(True)
                self.btn_play.setText("播放")
                self.btn_play.setChecked(False)
                self._set_playback_slider_position(0, self.playback.total_frames)
                self._update_scene_recon_button_state()
            else:
                self.lbl_play_status.setText("加载失败")
                self._set_playback_slider_position(0, 0)
                self._update_scene_recon_button_state()

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
        self._set_playback_slider_position(current, total)

    def on_playback_finished(self):
        self.btn_play.setChecked(False)
        self.btn_play.setText("播放")
        self.lbl_play_status.setText("播放结束")

    def _get_default_scene_recon_params(self):
        return {
            'max_range_m': float(getattr(config, 'SCENE_RECON_MAX_RANGE_M', 5000.0)),
            'horizontal_fov_deg': float(getattr(config, 'SCENE_RECON_LIDAR_H_FOV_DEG', 1.0)),
            'vertical_fov_deg': float(getattr(config, 'SCENE_RECON_LIDAR_V_FOV_DEG', 1.0)),
            'frame_stride': max(1, int(getattr(config, 'SCENE_RECON_FRAME_STRIDE', 25))),
            'servo_lag_frames': int(getattr(config, 'SCENE_RECON_SERVO_LAG_FRAMES', 0)),
            'forward_axis': str(getattr(config, 'SCENE_RECON_FORWARD_AXIS', 'Z')).strip().upper(),
            'icp_enabled': bool(getattr(config, 'SCENE_RECON_ICP_ENABLED', True)),
            'icp_voxel_size': float(getattr(config, 'SCENE_RECON_ICP_VOXEL_SIZE', 0.25)),
            'icp_max_corr_dist': float(getattr(config, 'SCENE_RECON_ICP_MAX_CORR_DIST', 0.8)),
            'icp_max_iterations': int(getattr(config, 'SCENE_RECON_ICP_MAX_ITERATIONS', 80)),
            'icp_min_fitness': float(getattr(config, 'SCENE_RECON_ICP_MIN_FITNESS', 0.12)),
            'icp_every_n_frames': max(1, int(getattr(config, 'SCENE_RECON_ICP_EVERY_N_FRAMES', 1))),
            'icp_map_window_frames': max(1, int(getattr(config, 'SCENE_RECON_ICP_MAP_WINDOW_FRAMES', 25))),
            'icp_min_points': max(1, int(getattr(config, 'SCENE_RECON_ICP_MIN_POINTS', 200))),
            'icp_prior_weight': float(getattr(config, 'SCENE_RECON_ICP_PRIOR_WEIGHT', 0.7)),
            'icp_max_delta_rot_deg': float(getattr(config, 'SCENE_RECON_ICP_MAX_DELTA_ROT_DEG', 2.5)),
            'icp_max_delta_trans_m': float(getattr(config, 'SCENE_RECON_ICP_MAX_DELTA_TRANS_M', 0.25)),
            'icp_reject_delta_rot_deg': float(getattr(config, 'SCENE_RECON_ICP_REJECT_DELTA_ROT_DEG', 8.0)),
            'icp_reject_delta_trans_m': float(getattr(config, 'SCENE_RECON_ICP_REJECT_DELTA_TRANS_M', 0.8)),
        }

    def show_scene_recon_settings_dialog(self):
        params = dict(self._scene_recon_params or self._get_default_scene_recon_params())
        if params.get('forward_axis') not in ('X', 'Y', 'Z'):
            params['forward_axis'] = 'Z'

        dialog = QDialog(self)
        dialog.setWindowTitle("场景重建参数")
        dialog.resize(520, 640)

        root_layout = QVBoxLayout(dialog)
        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        form = QFormLayout(content)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        widgets = {}

        def add_double(key, label, minimum, maximum, decimals, step):
            spin = QDoubleSpinBox(content)
            spin.setRange(minimum, maximum)
            spin.setDecimals(decimals)
            spin.setSingleStep(step)
            spin.setValue(float(params[key]))
            form.addRow(label, spin)
            widgets[key] = spin

        def add_int(key, label, minimum, maximum):
            spin = QSpinBox(content)
            spin.setRange(minimum, maximum)
            spin.setValue(int(params[key]))
            form.addRow(label, spin)
            widgets[key] = spin

        add_double('max_range_m', "最大距离 (m)", 0.1, 100000.0, 2, 10.0)
        add_double('horizontal_fov_deg', "水平视场角 (deg)", 0.001, 180.0, 3, 0.1)
        add_double('vertical_fov_deg', "垂直视场角 (deg)", 0.001, 180.0, 3, 0.1)
        add_int('frame_stride', "帧步长", 1, 100000)
        add_int('servo_lag_frames', "伺服延迟帧数", -100000, 100000)

        axis_combo = QComboBox(content)
        axis_combo.addItems(["X", "Y", "Z"])
        axis_combo.setCurrentText(params['forward_axis'])
        form.addRow("前向轴", axis_combo)
        widgets['forward_axis'] = axis_combo

        icp_enabled = QCheckBox("启用 ICP 精配准", content)
        icp_enabled.setChecked(bool(params['icp_enabled']))
        form.addRow("ICP", icp_enabled)
        widgets['icp_enabled'] = icp_enabled

        add_double('icp_voxel_size', "ICP 体素大小 (m)", 0.001, 100.0, 4, 0.05)
        add_double('icp_max_corr_dist', "ICP 最大对应距离 (m)", 0.001, 100.0, 4, 0.05)
        add_int('icp_max_iterations', "ICP 最大迭代次数", 1, 100000)
        add_double('icp_min_fitness', "ICP 最小 fitness", 0.0, 1.0, 4, 0.01)
        add_int('icp_every_n_frames', "ICP 间隔帧数", 1, 100000)
        add_int('icp_map_window_frames', "ICP 地图窗口帧数", 1, 100000)
        add_int('icp_min_points', "ICP 最小点数", 1, 10000000)
        add_double('icp_prior_weight', "ICP 先验权重", 0.0, 1.0, 4, 0.05)
        add_double('icp_max_delta_rot_deg', "ICP 最大修正角 (deg)", 0.0, 180.0, 3, 0.1)
        add_double('icp_max_delta_trans_m', "ICP 最大修正平移 (m)", 0.0, 1000.0, 4, 0.05)
        add_double('icp_reject_delta_rot_deg', "ICP 拒绝角阈值 (deg)", 0.0, 180.0, 3, 0.1)
        add_double('icp_reject_delta_trans_m', "ICP 拒绝平移阈值 (m)", 0.0, 1000.0, 4, 0.05)

        scroll.setWidget(content)
        root_layout.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        root_layout.addWidget(buttons)

        if dialog.exec_() != QDialog.Accepted:
            return None

        updated = {}
        for key, widget in widgets.items():
            if isinstance(widget, QDoubleSpinBox):
                updated[key] = float(widget.value())
            elif isinstance(widget, QSpinBox):
                updated[key] = int(widget.value())
            elif isinstance(widget, QCheckBox):
                updated[key] = bool(widget.isChecked())
            elif isinstance(widget, QComboBox):
                updated[key] = str(widget.currentText()).strip().upper()

        self._scene_recon_params = updated
        return updated

    def start_scene_reconstruction_from_playback(self):
        if self.receiving:
            self.statusBar().showMessage("请先断开实时连接后再执行场景重建", 5000)
            return
        if not self.playback.file_handle:
            self.statusBar().showMessage("请先加载 depth 录制文件", 5000)
            return
        if self.playback.data_type != 0:
            self.statusBar().showMessage("当前文件为 ToF 数据，不支持场景重建", 5000)
            return
        if not self.playback.filename:
            self.statusBar().showMessage("当前回放文件路径无效", 5000)
            return

        was_playing = self.playback.timer.isActive()
        if was_playing:
            self.playback.pause()
            self.btn_play.setChecked(False)
            self.btn_play.setText("播放")

        params = self.show_scene_recon_settings_dialog()
        if params is None:
            self.statusBar().showMessage("已取消场景重建", 3000)
            if was_playing and self.playback.file_handle:
                self.playback.start()
                self.btn_play.setChecked(True)
                self.btn_play.setText("暂停")
            return

        base, _ = os.path.splitext(self.playback.filename)
        output_path = base + ".ply"
        max_range_m = float(params['max_range_m'])
        horizontal_fov_deg = float(params['horizontal_fov_deg'])
        vertical_fov_deg = float(params['vertical_fov_deg'])
        frame_stride = max(1, int(params['frame_stride']))
        servo_lag_frames = int(params['servo_lag_frames'])
        forward_axis = str(params['forward_axis']).strip().upper()
        icp_enabled = bool(params['icp_enabled'])
        icp_voxel_size = float(params['icp_voxel_size'])
        icp_max_corr_dist = float(params['icp_max_corr_dist'])
        icp_max_iterations = int(params['icp_max_iterations'])
        icp_min_fitness = float(params['icp_min_fitness'])
        icp_every_n_frames = max(1, int(params['icp_every_n_frames']))
        icp_map_window_frames = max(1, int(params['icp_map_window_frames']))
        icp_min_points = max(1, int(params['icp_min_points']))
        icp_prior_weight = float(params['icp_prior_weight'])
        icp_max_delta_rot_deg = float(params['icp_max_delta_rot_deg'])
        icp_max_delta_trans_m = float(params['icp_max_delta_trans_m'])
        icp_reject_delta_rot_deg = float(params['icp_reject_delta_rot_deg'])
        icp_reject_delta_trans_m = float(params['icp_reject_delta_trans_m'])
        if forward_axis not in ('X', 'Y', 'Z'):
            forward_axis = 'Z'

        self.statusBar().showMessage("场景重建中，请稍候...", 3000)
        self._set_scene_recon_progress(0, max(1, self.playback.total_frames))
        if hasattr(self, 'btn_scene_recon'):
            self.btn_scene_recon.setEnabled(False)
        self.btn_load.setEnabled(False)
        self.btn_play.setEnabled(False)

        try:
            summary = export_scene_point_cloud(
                self.playback.iter_depth_frames(servo_lag_frames=servo_lag_frames),
                output_path,
                max_range_m=max_range_m,
                horizontal_fov_deg=horizontal_fov_deg,
                vertical_fov_deg=vertical_fov_deg,
                frame_stride=frame_stride,
                forward_axis=forward_axis,
                icp_enabled=icp_enabled,
                icp_voxel_size=icp_voxel_size,
                icp_max_corr_dist=icp_max_corr_dist,
                icp_max_iterations=icp_max_iterations,
                icp_min_fitness=icp_min_fitness,
                icp_every_n_frames=icp_every_n_frames,
                icp_map_window_frames=icp_map_window_frames,
                icp_min_points=icp_min_points,
                icp_prior_weight=icp_prior_weight,
                icp_max_delta_rot_deg=icp_max_delta_rot_deg,
                icp_max_delta_trans_m=icp_max_delta_trans_m,
                icp_reject_delta_rot_deg=icp_reject_delta_rot_deg,
                icp_reject_delta_trans_m=icp_reject_delta_trans_m,
                progress_callback=self._set_scene_recon_progress,
                total_frames_hint=self.playback.total_frames,
            )
            point_count = int(summary.get('points', 0))
            used_frames = int(summary.get('frames_used', 0))
            total_frames = int(summary.get('frames_total', 0))
            used_stride = int(summary.get('frame_stride', frame_stride))
            used_axis = str(summary.get('forward_axis', forward_axis))
            icp_applied = bool(summary.get('icp_applied', False))
            icp_requested = bool(summary.get('icp_requested', False))
            icp_attempted = int(summary.get('icp_attempted', 0))
            icp_succeeded = int(summary.get('icp_succeeded', 0))
            icp_failed = int(summary.get('icp_failed', 0))
            icp_rejected = int(summary.get('icp_rejected_delta', 0))
            icp_limited = int(summary.get('icp_limited_delta', 0))
            icp_fit = float(summary.get('icp_avg_fitness', 0.0))
            icp_rmse = float(summary.get('icp_avg_rmse', 0.0))
            icp_delta_rot = float(summary.get('icp_avg_applied_delta_rot', 0.0))
            icp_delta_trans = float(summary.get('icp_avg_applied_delta_trans', 0.0))
            self._set_scene_recon_progress(total_frames, max(1, total_frames))
            self.lbl_play_status.setText(
                f"场景重建完成: {used_frames}/{total_frames} 帧 (stride={used_stride}, lag={servo_lag_frames}, axis={used_axis}), {point_count} 点"
            )
            if icp_applied:
                self.statusBar().showMessage(
                    f"点云导出成功: {output_path} | ICP {icp_succeeded}/{icp_attempted} 成功, 失败 {icp_failed}, reject={icp_rejected}, limit={icp_limited}, fit={icp_fit:.3f}, rmse={icp_rmse:.4f}, dR={icp_delta_rot:.2f}deg, dT={icp_delta_trans:.3f}m",
                    10000,
                )
            elif icp_requested:
                self.statusBar().showMessage(
                    f"点云导出成功: {output_path} | ICP 未生效（可能缺少 Open3D）",
                    10000,
                )
            else:
                self.statusBar().showMessage(f"点云导出成功: {output_path}", 8000)

            self.show_reconstructed_point_cloud(output_path)
        except Exception as e:
            self._set_scene_recon_progress(0, max(1, self.playback.total_frames))
            self.lbl_play_status.setText("场景重建失败")
            self.statusBar().showMessage(f"场景重建失败: {e}", 8000)
        finally:
            self.btn_load.setEnabled(True)
            self.btn_play.setEnabled(True)
            self._update_scene_recon_button_state()
            if was_playing and self.playback.file_handle:
                self.playback.start()
                self.btn_play.setChecked(True)
                self.btn_play.setText("暂停")

    def show_reconstructed_point_cloud(self, ply_path):
        if not HAS_OPEN3D:
            self.statusBar().showMessage("无法打开点云窗口：未安装 Open3D", 8000)
            return
        if not ply_path or not os.path.exists(ply_path):
            self.statusBar().showMessage("无法打开点云窗口：PLY 文件不存在", 8000)
            return

        try:
            pcd = o3d.io.read_point_cloud(ply_path)
            if pcd is None or len(pcd.points) == 0:
                self.statusBar().showMessage("无法打开点云窗口：点云为空", 8000)
                return

            self.statusBar().showMessage("正在打开 Open3D 点云窗口...", 3000)
            o3d.visualization.draw_geometries([pcd], window_name="重建点云")
        except Exception as e:
            self.statusBar().showMessage(f"打开点云窗口失败: {e}", 8000)

    def update_img_levels(self, img, min_val, max_val):
        img.setLevels([min_val, max_val])

    # 保存录制文件按钮处理逻辑
    def select_recording_path(self):
        directory = QFileDialog.getExistingDirectory(self, "选择保存文件夹", "")
        if directory:
            self.txt_rec_path.setText(directory)

    def _resolve_export_lut(self, img_item, hist_item=None, fallback_cmap=None):
        lut = getattr(img_item, 'lut', None)
        if lut is None and hist_item is not None:
            try:
                if hasattr(hist_item, 'getLookupTable'):
                    lut = hist_item.getLookupTable(n=256, alpha=False)
            except Exception:
                lut = None

            if lut is None and hasattr(hist_item, 'gradient'):
                try:
                    lut = hist_item.gradient.getLookupTable(256, alpha=False)
                except Exception:
                    lut = None
                if lut is None:
                    try:
                        cmap_obj = hist_item.gradient.colorMap()
                        if cmap_obj is not None:
                            lut = cmap_obj.getLookupTable(0.0, 1.0, 256)
                    except Exception:
                        lut = None

        if lut is None and fallback_cmap:
            try:
                cmap_obj = get_colormap(fallback_cmap)
                lut = cmap_obj.getLookupTable(0.0, 1.0, 256)
            except Exception:
                lut = None

        return lut

    def _save_rendered_matrix(self, arr_xy, output_path, levels=None, lut=None, fallback_cmap=None):
        try:
            arr = np.asarray(arr_xy)
            arr = np.squeeze(arr)
            if arr.ndim != 2:
                return False, f"不支持的图像维度: {arr.shape}"

            # arr_xy is in ImageItem input orientation (x,y). Export image uses (y,x).
            arr = arr.T
            arr = np.nan_to_num(arr.astype(np.float32, copy=False), nan=0.0, posinf=0.0, neginf=0.0)

            if levels is None or len(levels) < 2:
                lo = float(np.min(arr)) if arr.size else 0.0
                hi = float(np.max(arr)) if arr.size else 1.0
            else:
                lo, hi = float(levels[0]), float(levels[1])
            if not np.isfinite(lo):
                lo = 0.0
            if not np.isfinite(hi) or hi <= lo:
                hi = lo + 1.0

            norm = np.clip((arr - lo) / (hi - lo), 0.0, 1.0)
            idx = (norm * 255.0).astype(np.uint8)

            def _normalize_lut(lut_val):
                if lut_val is None:
                    return None
                lut_arr = np.asarray(lut_val)
                if lut_arr.ndim == 1:
                    lut_arr = np.stack([lut_arr, lut_arr, lut_arr], axis=1)
                elif lut_arr.ndim == 2 and lut_arr.shape[1] == 1:
                    lut_arr = np.repeat(lut_arr, 3, axis=1)
                if not (lut_arr.ndim == 2 and lut_arr.shape[0] > 0 and lut_arr.shape[1] >= 3):
                    return None
                lut_arr = lut_arr[:, :3]
                if np.issubdtype(lut_arr.dtype, np.floating):
                    maxv = float(np.nanmax(lut_arr)) if lut_arr.size else 0.0
                    if maxv <= 1.0:
                        lut_arr = lut_arr * 255.0
                lut_arr = np.clip(lut_arr, 0, 255).astype(np.uint8, copy=False)
                if lut_arr.shape[0] != 256:
                    sample_idx = np.linspace(0, lut_arr.shape[0] - 1, 256).astype(np.int32)
                    lut_arr = lut_arr[sample_idx]
                return lut_arr

            lut_arr = _normalize_lut(lut)
            if lut_arr is None and fallback_cmap:
                try:
                    cmap_obj = get_colormap(fallback_cmap)
                    lut_arr = _normalize_lut(cmap_obj.getLookupTable(0.0, 1.0, 256))
                except Exception:
                    lut_arr = None

            if lut_arr is not None and np.all(lut_arr[:, 0] == lut_arr[:, 1]) and np.all(lut_arr[:, 1] == lut_arr[:, 2]) and fallback_cmap:
                try:
                    cmap_obj = get_colormap(fallback_cmap)
                    fb_lut = _normalize_lut(cmap_obj.getLookupTable(0.0, 1.0, 256))
                    if fb_lut is not None:
                        lut_arr = fb_lut
                except Exception:
                    pass

            if lut_arr is None:
                gray = idx
                rgb = np.stack([gray, gray, gray], axis=-1)
            else:
                rgb = lut_arr[idx]

            rgb = np.ascontiguousarray(rgb)
            h, w = rgb.shape[0], rgb.shape[1]
            bytes_per_line = rgb.strides[0]
            qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()
            if not qimg.save(output_path, "PNG"):
                return False, f"保存失败: {output_path}"
            return True, "ok"
        except Exception as e:
            return False, str(e)

    def _save_rendered_image_item(self, img_item, output_path, hist_item=None, fallback_cmap=None):
        img_data = getattr(img_item, 'image', None)
        if img_data is None:
            return False, "当前无图像数据"
        levels = img_item.getLevels()
        lut = self._resolve_export_lut(img_item, hist_item, fallback_cmap)
        return self._save_rendered_matrix(img_data, output_path, levels=levels, lut=lut, fallback_cmap=fallback_cmap)

    def export_current_images(self):
        export_stride = 20
        playback_mode = (not self.receiving) and bool(self.playback and self.playback.file_handle)

        directory = QFileDialog.getExistingDirectory(self, "选择图像导出目录", "")
        if not directory:
            return

        if playback_mode and self.playback.data_type == 0:
            int_hist = getattr(self.glw_int, 'hist_lut_item', None) if hasattr(self, 'glw_int') else None
            rng_hist = getattr(self.glw_rng, 'hist_lut_item', None) if hasattr(self, 'glw_rng') else None

            int_levels = self.img_int.getLevels() if hasattr(self, 'img_int') else None
            rng_levels = self.img_rng.getLevels() if hasattr(self, 'img_rng') else None
            int_lut = self._resolve_export_lut(self.img_int, int_hist, getattr(config, 'DEFAULT_INTENSITY_CMAP', None)) if hasattr(self, 'img_int') else None
            rng_lut = self._resolve_export_lut(self.img_rng, rng_hist, getattr(config, 'DEFAULT_RANGE_CMAP', None)) if hasattr(self, 'img_rng') else None

            saved_count = 0
            skipped_count = 0
            failed_count = 0

            ts = time.strftime("%Y%m%d_%H%M%S")
            try:
                for intensity, rng, meta in self.playback.iter_depth_frames():
                    frame_idx = int(meta.get('frame_index', 0))
                    frame_1based = frame_idx + 1
                    if (frame_1based - 1) % export_stride != 0:
                        skipped_count += 1
                        continue

                    # Keep rendering path identical to display: process first, then transpose for ImageItem.
                    proc_int, proc_rng = self.processor.process(intensity, rng)
                    int_xy = proc_int.T
                    rng_xy = proc_rng.T

                    int_path = os.path.join(directory, f"intensity_{ts}_f{frame_1based:06d}.png")
                    rng_path = os.path.join(directory, f"range_{ts}_f{frame_1based:06d}.png")

                    ok_int, _ = self._save_rendered_matrix(
                        int_xy,
                        int_path,
                        levels=int_levels,
                        lut=int_lut,
                        fallback_cmap=getattr(config, 'DEFAULT_INTENSITY_CMAP', None),
                    )
                    ok_rng, _ = self._save_rendered_matrix(
                        rng_xy,
                        rng_path,
                        levels=rng_levels,
                        lut=rng_lut,
                        fallback_cmap=getattr(config, 'DEFAULT_RANGE_CMAP', None),
                    )

                    if ok_int and ok_rng:
                        saved_count += 1
                    else:
                        failed_count += 1

                self.statusBar().showMessage(
                    f"回放批量导出完成(每{export_stride}帧保存1帧, 从1开始): 保存{saved_count} 跳过{skipped_count} 失败{failed_count}",
                    8000,
                )
            except Exception as e:
                self.statusBar().showMessage(f"回放批量导出失败: {e}", 8000)
            return

        if playback_mode:
            displayed_frame = max(0, int(getattr(self.playback, 'current_frame', 0)) - 1)
            displayed_frame_1based = displayed_frame + 1
            if (displayed_frame_1based - 1) % export_stride != 0:
                self.statusBar().showMessage(
                    f"回放导出按每{export_stride}帧保存1帧（从1开始）：当前第{displayed_frame_1based}帧已跳过",
                    3000,
                )
                return

        int_data = getattr(self.img_int, 'image', None) if hasattr(self, 'img_int') else None
        rng_data = getattr(self.img_rng, 'image', None) if hasattr(self, 'img_rng') else None
        if int_data is None and rng_data is None:
            self.statusBar().showMessage("导出失败：当前没有可导出的强度/距离图像", 5000)
            return

        ts = time.strftime("%Y%m%d_%H%M%S")
        int_path = os.path.join(directory, f"intensity_{ts}.png")
        rng_path = os.path.join(directory, f"range_{ts}.png")

        int_hist = getattr(self.glw_int, 'hist_lut_item', None) if hasattr(self, 'glw_int') else None
        rng_hist = getattr(self.glw_rng, 'hist_lut_item', None) if hasattr(self, 'glw_rng') else None

        ok_int, msg_int = self._save_rendered_image_item(
            self.img_int,
            int_path,
            int_hist,
            getattr(config, 'DEFAULT_INTENSITY_CMAP', None),
        ) if int_data is not None else (False, "当前无强度图")
        ok_rng, msg_rng = self._save_rendered_image_item(
            self.img_rng,
            rng_path,
            rng_hist,
            getattr(config, 'DEFAULT_RANGE_CMAP', None),
        ) if rng_data is not None else (False, "当前无距离图")

        if ok_int and ok_rng:
            if playback_mode:
                self.statusBar().showMessage(
                    f"回放导出成功(stride={export_stride}): {int_path} | {rng_path}",
                    6000,
                )
            else:
                self.statusBar().showMessage(f"导出成功: {int_path} | {rng_path}", 6000)
            return

        errors = []
        if not ok_int:
            errors.append(f"强度图失败({msg_int})")
        if not ok_rng:
            errors.append(f"距离图失败({msg_rng})")
        self.statusBar().showMessage("导出完成但存在错误: " + "；".join(errors), 6000)

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

    def update_runtime_diag(self):
        now = time.time()
        dt = max(1e-6, now - self._diag_last_ts)

        with self._frame_lock:
            rx_int = self._rx_int_frames
            rx_tof = self._rx_tof_frames
            ui_int = self._ui_int_frames
            ui_tof = self._ui_tof_frames
            pending_int = 1 if self._latest_int_rng is not None else 0
            pending_tof = 1 if self._latest_tof is not None else 0

        rx_int_fps = (rx_int - self._diag_last_rx_int) / dt
        rx_tof_fps = (rx_tof - self._diag_last_rx_tof) / dt
        ui_int_fps = (ui_int - self._diag_last_ui_int) / dt
        ui_tof_fps = (ui_tof - self._diag_last_ui_tof) / dt

        self._diag_last_ts = now
        self._diag_last_rx_int = rx_int
        self._diag_last_rx_tof = rx_tof
        self._diag_last_ui_int = ui_int
        self._diag_last_ui_tof = ui_tof

        frag_tasks = 0
        frag_buf = 0
        if self.receiver:
            try:
                frag_stats = self.receiver.get_fragment_stats()
                frag_tasks = int(frag_stats.get('active_tasks', 0))
                frag_buf = int(frag_stats.get('buffered_fragments', 0))
            except Exception:
                pass

        qsize = 0
        dropped = 0
        try:
            rec_metrics = self.recorder.get_metrics()
            qsize = int(rec_metrics.get('queue_size', 0))
            dropped = int(rec_metrics.get('frames_dropped', 0))
        except Exception:
            pass

        bias_text = "偏压时延(首应答/到位):--/-- ms"
        if self._bias_diag['send_time'] > 0:
            first_ms = "--"
            done_ms = "--"
            if self._bias_diag['first_ack'] > 0:
                first_ms = f"{(self._bias_diag['first_ack'] - self._bias_diag['send_time']) * 1000:.0f}"
            if self._bias_diag['done_time'] > 0:
                done_ms = f"{(self._bias_diag['done_time'] - self._bias_diag['send_time']) * 1000:.0f}"
            bias_text = f"偏压时延(首应答/到位):{first_ms}/{done_ms} ms"

        self.lbl_runtime_diag.setText(
            f"接收帧率(强/ToF):{rx_int_fps:.1f}/{rx_tof_fps:.1f} "
            f"显示帧率(强/ToF):{ui_int_fps:.1f}/{ui_tof_fps:.1f} "
            f"待处理(强/ToF):{pending_int}/{pending_tof} "
            f"分片(任务/缓存):{frag_tasks}/{frag_buf} "
            f"录制队列:{qsize} 丢帧:{dropped} "
            f"{bias_text}"
        )

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
            self._update_center_cross(self.img_int, self.cross_int)
            self._update_center_cross(self.img_rng, self.cross_rng)
            self.lbl_pixel_info.setText("重建完成 (已应用后处理)")
        else:
            self.img_int.setImage(self.raw_recon_int.T, autoLevels=False)
            self.img_rng.setImage(self.raw_recon_rng.T, autoLevels=False)
            self._update_center_cross(self.img_int, self.cross_int)
            self._update_center_cross(self.img_rng, self.cross_rng)
            self.lbl_pixel_info.setText("重建完成 (原始数据)")

    # 后处理完成后的显示更新（实时流和重建后处理共用）
    def update_display_int_rng(self, intensity, rng, task_id=None, pitch=0.0, yaw=0.0):
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

        # Update Servo
        servo_text = f"Pitch: {pitch:.2f}, Yaw: {yaw:.2f}"
        self.txt_servo_int.setText(servo_text)
        self.txt_servo_rng.setText(servo_text)

        self.img_int.setImage(intensity.T, autoLevels=False)
        self.img_rng.setImage(rng.T, autoLevels=False)
        self._update_center_cross(self.img_int, self.cross_int)
        self._update_center_cross(self.img_rng, self.cross_rng)

        hist_interval = float(getattr(config, 'HIST_UPDATE_INTERVAL_SEC', 0.2))
        if curr_time - self.last_hist_update_int_rng >= hist_interval:
            self.last_hist_update_int_rng = curr_time
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
    def update_display_tof(self, tof, task_id=None, pitch=0.0, yaw=0.0):
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

        # Update Servo
        self.txt_servo_tof.setText(f"Pitch: {pitch:.2f}, Yaw: {yaw:.2f}")

        self.img_tof.setImage(tof.T, autoLevels=False)
        hist_interval = float(getattr(config, 'HIST_UPDATE_INTERVAL_SEC', 0.2))
        if curr_time - self.last_hist_update_tof >= hist_interval:
            self.last_hist_update_tof = curr_time
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
            # Temp: 223 - 263, 1 decimal place? (0.1K)
            val_temp = QDoubleValidator(223.0, 263.0, 1, self)
            val_temp.setNotation(QDoubleValidator.StandardNotation)
            self.txt_set_temp.setValidator(val_temp)
            self.txt_set_temp.setPlaceholderText("223-263")

        if hasattr(self, 'txt_set_bias'):
            # Bias: 5 - 81, 1 decimal place?
            val_bias = QDoubleValidator(5.0, 81, 1, self)
            val_bias.setNotation(QDoubleValidator.StandardNotation)
            self.txt_set_bias.setValidator(val_bias)
            self.txt_set_bias.setPlaceholderText("5-81")

        if hasattr(self, 'grp_serial_log'):
            try:
                self.txt_serial_log.document().setMaximumBlockCount(int(getattr(config, 'SERIAL_LOG_MAX_BLOCKS', 2000)))
            except Exception:
                pass
            if hasattr(self, 'btn_export_log'):
                self.btn_export_log.clicked.connect(self.export_serial_log)

    def export_serial_log(self):
        directory = QFileDialog.getExistingDirectory(self, "选择日志保存目录", "")
        if not directory:
            return

        port = self.combo_port.currentData() if hasattr(self, 'combo_port') else None
        port_name = str(port) if port else "SERIAL"
        safe_port = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in port_name)
        filename = f"{safe_port}_{time.strftime('%Y%m%d_%H%M%S')}.log"
        path = os.path.join(directory, filename)

        try:
            self.serial_worker.start_log_stream(path)
            self._serial_log_stream_path = path
            self.statusBar().showMessage(f"已开始流式导出日志: {path}", 5000)
        except Exception as e:
            self.statusBar().showMessage(f"日志导出启动失败: {e}", 5000)

    def reset_cmd_ui_inputs(self):
        # 按需求在关闭串口后将发送命令相关输入恢复为0/默认。
        if hasattr(self, 'txt_set_temp'):
            self.txt_set_temp.setText("0")
        if hasattr(self, 'txt_set_bias'):
            self.txt_set_bias.setText("0")

        if hasattr(self, 'chk_apd_trig'):
            self.chk_apd_trig.setChecked(False)
        if hasattr(self, 'chk_apd_test_point'):
            self.chk_apd_test_point.setChecked(False)
        if hasattr(self, 'chk_apd_test_mode'):
            self.chk_apd_test_mode.setChecked(False)

        if hasattr(self, 'sb_algo_frames'):
            self.sb_algo_frames.setValue(0)
        if hasattr(self, 'sb_algo_noise'):
            self.sb_algo_noise.setValue(0)
        if hasattr(self, 'sb_algo_step'):
            self.sb_algo_step.setValue(0)
        if hasattr(self, 'sb_algo_thresh'):
            self.sb_algo_thresh.setValue(0)
        if hasattr(self, 'sb_algo_kernel'):
            self.sb_algo_kernel.setValue(0)

        if hasattr(self, 'sb_proj_dist'):
            self.sb_proj_dist.setValue(0)
        if hasattr(self, 'sb_proj_vel'):
            self.sb_proj_vel.setValue(0)

        self.last_cmd_name = "--"
        if hasattr(self, 'lbl_recv_cmd_type'):
            self.lbl_recv_cmd_type.setText("--")
        if hasattr(self, 'lbl_recv_result'):
            self.lbl_recv_result.setStyleSheet("font-weight: bold; color: gray;")
            self.lbl_recv_result.setText("--")

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
            self.reset_cmd_ui_inputs()
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
            self.statusBar().showMessage("错误：温度必须是数字", 3000)
            return

        if not (223 <= temp_k <= 263):
            self.statusBar().showMessage("错误：温度必须在 223K 到 263K 之间", 3000)
            return

        val = int(temp_k)
        self.serial_worker.protocol.set_temp(val)
        self.statusBar().showMessage(f"已发送温度：{temp_k}K", 3000)

    def send_bias_cmd(self):
        txt = self.txt_set_bias.text().strip()
        try:
            val = float(txt)
        except ValueError:
            self.statusBar().showMessage("错误：电压必须是数字", 3000)
            return

        if not (5 <= val <= 81):
            self.statusBar().showMessage("错误：电压必须在 5V 到 81V 之间", 3000)
            return

        # Bias latency diagnostics start point.
        self._bias_diag['target'] = val
        self._bias_diag['send_time'] = time.time()
        self._bias_diag['first_ack'] = 0.0
        self._bias_diag['done_time'] = 0.0

        v_int = int(val)
        v_dec = int(round((val - v_int) * 10))
        self.serial_worker.protocol.set_bias(v_int, v_dec)
        self.statusBar().showMessage(f"已发送偏压：{val}V", 3000)

    def send_algo_cmd(self):
        f = self.sb_algo_frames.value() & 0x0F
        n = self.sb_algo_noise.value() & 0x0F
        s = self.sb_algo_step.value() & 0x0F
        t = self.sb_algo_thresh.value() & 0x0F
        k = self.sb_algo_kernel.value() & 0xFF

        self.serial_worker.protocol.set_algo(f, n, s, t, k)
        self.statusBar().showMessage("已发送算法配置", 3000)

    def send_proj_cmd(self):
        dist = int(self.sb_proj_dist.value())
        vel = int(self.sb_proj_vel.value())

        self.serial_worker.protocol.set_proj_info(dist, vel)
        self.statusBar().showMessage(f"已发送弹体信息：{dist}m, {vel}m/s", 3000)

    def send_apd_config_cmd(self):
        trig = self.chk_apd_trig.isChecked()
        test_pt = self.chk_apd_test_point.isChecked()
        test_mode = self.chk_apd_test_mode.isChecked()
        self.serial_worker.protocol.set_apd_config(trig, test_pt, test_mode)
        self.statusBar().showMessage("已发送 APD 配置", 3000)

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

        # Update bias response/settling diagnostics.
        if self._bias_diag['send_time'] > 0:
            now = time.time()
            if self._bias_diag['first_ack'] == 0.0:
                self._bias_diag['first_ack'] = now
            if self._bias_diag['done_time'] == 0.0 and abs(float(volt) - float(self._bias_diag['target'])) <= 0.2:
                self._bias_diag['done_time'] = now

        # Build status string for failures
        failures = []
        last_cmd = getattr(self, 'last_cmd_name', '')

        if last_cmd == '算法配置' and data.get('algo_status') == 1: failures.append("算法")
        if last_cmd == '设置偏压' and data.get('apd_bias_status') == 1: failures.append("偏压")
        if last_cmd == 'APD配置' and data.get('apd_ctrl_status') == 1: failures.append("APD控制")
        if '测试' in last_cmd and data.get('test_status') == 1: failures.append("测试")

        # 电源状态: byte 12，低位=制冷机，高位=探测器(APD)
        power_st = data.get('power_status', 0)
        cooler_on = (power_st & 0x01) == 0
        apd_on = (power_st & 0x02) == 0
        cooler_state = "上电" if cooler_on else "下电"
        apd_state = "上电" if apd_on else "下电"
        is_power_cmd = last_cmd in ["制冷机上电", "制冷机下电", "探测器上电", "探测器下电"]

        status_msg = (
            f"电源状态: 制冷机{cooler_state}, 探测器{apd_state}"
            if is_power_cmd
            else ("失败: " + ", ".join(failures) if failures else "全部正常")
        )

        if hasattr(self, 'lbl_recv_cmd_type'):
            self.lbl_recv_cmd_type.setText(getattr(self, 'last_cmd_name', '--'))

            if is_power_cmd:
                if last_cmd in ["制冷机上电", "制冷机下电"]:
                    res_str = cooler_state
                else:
                    res_str = apd_state
                color = "green" if res_str == "上电" else "gray"
                self.lbl_recv_result.setStyleSheet(f"font-weight: bold; color: {color};")
            elif failures:
                res_str = "失败"
                self.lbl_recv_result.setStyleSheet("font-weight: bold; color: red;")
            else:
                res_str = "成功"
                self.lbl_recv_result.setStyleSheet("font-weight: bold; color: green;")

            self.lbl_recv_result.setText(res_str)

            if hasattr(self, 'lbl_recv_temp'):
                self.lbl_recv_temp.setText(f"{temp} K")
            if hasattr(self, 'lbl_recv_volt'):
                self.lbl_recv_volt.setText(f"{volt:.1f} V")
            if hasattr(self, 'lbl_id_result'):
                self.lbl_id_result.setText(f"{version}")

if __name__ == "__main__":
    app = QApplication(sys.argv)

    apply_dark_theme(app)

    pg.setConfigOption('background', '#1e1e1e')

    pg.setConfigOption('background', 'k')
    pg.setConfigOption('foreground', '#dcdcdc')

    w = MainWindow()
    w.show()
    sys.exit(app.exec_())
