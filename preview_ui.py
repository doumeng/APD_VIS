import sys
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QGridLayout, QLabel, QLineEdit, QSpinBox, QPushButton, 
                             QCheckBox, QGroupBox, QTabWidget, QSplitter, QFrame, QSizePolicy)
from PyQt5.QtCore import Qt
import pyqtgraph as pg

# Configure pyqtgraph global settings
pg.setConfigOption('background', 'k')
pg.setConfigOption('foreground', 'w')

class PreviewWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("单光子激光雷达上位机 v3 (Split Layout Preview)")
        self.resize(1280, 800)
        
        self.init_ui()
        # Fullscreen or Maximized
        self.showMaximized()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Splitter for Left (Display) and Right (Config)
        splitter = QSplitter(Qt.Horizontal)
        
        # --- Left Side: Visualization (62%) ---
        self.tabs = QTabWidget()
        self.setup_display_tabs()
        splitter.addWidget(self.tabs)
        
        # --- Right Side: Configuration (30%) ---
        config_panel = QWidget()
        self.setup_config_panel(config_panel)
        splitter.addWidget(config_panel)
        
        # Set Splitter Ratio (Left 70%, Right 30%)
        splitter.setSizes([700, 300])
        
        main_layout.addWidget(splitter)

    def setup_display_tabs(self):
        # ... (display tabs logic) ...
        # Tab 1: Intensity & Range (2x2 Layout)
        tab_int_rng = QWidget()
        layout_tab1 = QGridLayout(tab_int_rng)
        
        # (0,0) Intensity Image
        layout_tab1.addWidget(self.create_image_group("强度图像 (Intensity)"), 0, 0)
        
        # (0,1) Range Image
        layout_tab1.addWidget(self.create_image_group("距离图像 (Range)"), 0, 1)
        
        # (1,0) Intensity Hist
        layout_tab1.addWidget(self.create_hist_group("强度分布直方图"), 1, 0)
        
        # (1,1) Range Hist
        layout_tab1.addWidget(self.create_hist_group("距离分布直方图"), 1, 1)
        
        self.tabs.addTab(tab_int_rng, "实时监控 (强度/距离)")
        
        # Tab 2: ToF (1x2 Layout)
        tab_tof = QWidget()
        layout_tab2 = QHBoxLayout(tab_tof)
        
        # Left: Image (Added Min/Max controls here as requested)
        layout_tab2.addWidget(self.create_image_group("ToF 图像"), stretch=2)
        
        # Right: Hist
        layout_tab2.addWidget(self.create_hist_group("ToF 统计直方图"), stretch=1)
        
        self.tabs.addTab(tab_tof, "光子飞行时间 (ToF)")

    def create_image_group(self, title):
        grp = QGroupBox(title)
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Params row
        l_params = QHBoxLayout()
        l_params.addWidget(QLabel("Range:"))
        l_params.addWidget(QSpinBox()) # Min
        l_params.addWidget(QLabel("-"))
        sb_max = QSpinBox()
        sb_max.setValue(255)
        l_params.addWidget(sb_max) # Max
        l_params.addStretch()
        layout.addLayout(l_params)
        
        # Plot
        glw = pg.GraphicsLayoutWidget()
        vb = glw.addViewBox()
        vb.setAspectLocked(True)
        img = pg.ImageItem()
        vb.addItem(img)
        layout.addWidget(glw)
        
        grp.setLayout(layout)
        return grp

    def create_hist_group(self, title):
        grp = QGroupBox(title)
        grp.setMaximumHeight(250)
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        pw = pg.PlotWidget(title=title)
        layout.addWidget(pw)
        grp.setLayout(layout)
        return grp

    def setup_config_panel(self, parent):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Use a TabWidget for Configuration
        config_tabs = QTabWidget()
        
        # --- Tab 1: Basic (Network & Record) ---
        tab_basic = QWidget()
        l_basic = QVBoxLayout(tab_basic)
        
        # 1. Network Config
        grp_net = QGroupBox("网络配置")
        l_net = QVBoxLayout()
        l_ip = QHBoxLayout()
        l_ip.addWidget(QLabel("IP:"))
        l_ip.addWidget(QLineEdit("127.0.0.1"))
        l_net.addLayout(l_ip)
        l_port = QHBoxLayout()
        l_port.addWidget(QLabel("Port:"))
        sb_port = QSpinBox()
        sb_port.setRange(1024, 65535)
        sb_port.setValue(5005)
        l_port.addWidget(sb_port)
        l_net.addLayout(l_port)
        btn_connect = QPushButton("连接")
        btn_connect.setFixedHeight(40)
        l_net.addWidget(btn_connect)
        grp_net.setLayout(l_net)
        l_basic.addWidget(grp_net)
        
        # 2. Recording
        grp_rec = QGroupBox("数据录制")
        l_rec = QVBoxLayout()
        l_rec.addWidget(QLabel("状态: 空闲 (00:00:00)"))
        btn_rec = QPushButton("开始录制")
        btn_rec.setCheckable(True)
        btn_rec.setFixedHeight(40)
        l_rec.addWidget(btn_rec)
        grp_rec.setLayout(l_rec)
        l_basic.addWidget(grp_rec)
        
        l_basic.addStretch()
        config_tabs.addTab(tab_basic, "基础配置")
        
        # --- Tab 2: Algorithm ---
        tab_algo = QWidget()
        l_algo = QVBoxLayout(tab_algo)
        
        grp_algo = QGroupBox("算法开关")
        la_in = QVBoxLayout()
        la_in.addWidget(QCheckBox("启用实时去噪"))
        la_in.addWidget(QCheckBox("自动增益控制 (AGC)"))
        grp_algo.setLayout(la_in)
        l_algo.addWidget(grp_algo)
        
        l_algo.addStretch()
        config_tabs.addTab(tab_algo, "算法设置")
        
        # --- Tab 3: Offline ---
        tab_off = QWidget()
        l_off = QVBoxLayout(tab_off)
        
        grp_off = QGroupBox("离线重建")
        lo_in = QVBoxLayout()
        lo_in.addWidget(QLabel("历史数据:"))
        lo_in.addWidget(QLineEdit("..."))
        lo_in.addWidget(QPushButton("浏览文件..."))
        lo_in.addSpacing(10)
        lo_in.addWidget(QPushButton("开始重建"))
        grp_off.setLayout(lo_in)
        l_off.addWidget(grp_off)
        
        l_off.addStretch()
        config_tabs.addTab(tab_off, "离线分析")
        
        layout.addWidget(config_tabs)
        layout.addWidget(QLabel("System Ready"))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PreviewWindow()
    window.show()
    sys.exit(app.exec_())