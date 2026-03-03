from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPalette, QColor

def apply_dark_theme(app):
    """
    Apply a dark theme using the Fusion style and custom palette.
    This mimics a modern Linux desktop look (e.g., KDE Breeze Dark, Adwaita Dark).
    """
    app.setStyle("Fusion")
    
    dark_palette = QPalette()
    
    # Base colors
    color_dark_bg = QColor(45, 45, 45)
    color_darker_bg = QColor(35, 35, 35)
    color_text = QColor(220, 220, 220)
    color_highlight = QColor(42, 130, 218) # Ubuntu/Adwaita Blue-ish
    color_red = QColor(255, 100, 100)
    
    dark_palette.setColor(QPalette.Window, color_dark_bg)
    dark_palette.setColor(QPalette.WindowText, color_text)
    dark_palette.setColor(QPalette.Base, color_darker_bg)
    dark_palette.setColor(QPalette.AlternateBase, color_dark_bg)
    dark_palette.setColor(QPalette.ToolTipBase, color_text)
    dark_palette.setColor(QPalette.ToolTipText, color_text)
    dark_palette.setColor(QPalette.Text, color_text)
    dark_palette.setColor(QPalette.Button, color_dark_bg)
    dark_palette.setColor(QPalette.ButtonText, color_text)
    dark_palette.setColor(QPalette.BrightText, color_red)
    dark_palette.setColor(QPalette.Link, color_highlight)
    dark_palette.setColor(QPalette.Highlight, color_highlight)
    dark_palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    
    # Disabled state
    dark_palette.setColor(QPalette.Disabled, QPalette.Text, QColor(127, 127, 127))
    dark_palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(127, 127, 127))

    app.setPalette(dark_palette)
    
    # Additional Stylesheet tweaks for specific widgets
    # Clean up GroupBox borders, Tab styling, and Input fields
    app.setStyleSheet("""
        QToolTip { 
            color: #ffffff; 
            background-color: #2a82da; 
            border: 1px solid white; 
        }
        QMainWindow {
            background-color: #2d2d2d;
        }
        QGroupBox {
            border: 1px solid #555555;
            border-radius: 5px;
            margin-top: 20px;
            font-weight: bold;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 5px;
            color: #aaaaaa;
        }
        QTabWidget::pane { 
            border: 1px solid #555555; 
            background-color: #2d2d2d;
        }
        QTabBar::tab {
            background: #353535;
            color: #aaaaaa;
            padding: 8px 12px;
            border: 1px solid #444444;
            border-bottom-color: #555555; /* Same as pane border */
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
        }
        QTabBar::tab:selected {
            background: #454545;
            color: #ffffff;
            border-bottom-color: #454545; /* Blend with pane */
        }
        QTabBar::tab:hover {
            background: #3a3a3a;
        }
        QLineEdit, QSpinBox, QComboBox {
            background-color: #1e1e1e;
            color: #dddddd;
            border: 1px solid #555555;
            padding: 4px;
            border-radius: 3px;
        }
        QPushButton {
            background-color: #3a3a3a;
            color: #dddddd;
            border: 1px solid #555555;
            padding: 6px 12px;
            border-radius: 4px;
        }
        QPushButton:hover {
            background-color: #454545;
            border-color: #2a82da;
        }
        QPushButton:pressed {
            background-color: #2a82da;
            color: white;
        }
        QPushButton:checked {
            background-color: #2a82da;
            color: white;
            border: 1px solid #2a82da;
        }
        QSplitter::handle {
            background-color: #444444;
        }
        QProgressBar {
            border: 1px solid #555555;
            border-radius: 3px;
            text-align: center;
            background-color: #1e1e1e;
        }
        QProgressBar::chunk {
            background-color: #2a82da;
            width: 10px; 
            margin: 0.5px;
        }
        QCheckBox {
            spacing: 5px;
            color: #dddddd;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
        }
    """)
