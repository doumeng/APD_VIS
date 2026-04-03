# Copilot Instructions

## Build, Test, and Lint

- **Install Dependencies**: `pip install -r requirements.txt`
- **Run Application**: `python main.py`
- **Run Simulators**: 
  - Intensity/Range data: `python utils/start_intensity_simulator.py`
  - ToF data: `python utils/start_tof_simulator.py`
  - *Note: Launch the main application and click "Connect" before starting simulators.*
- **Other Scripts**:
  - `test.py`: Utility script for parsing Excel files (not unit tests).

## High-Level Architecture

The application follows a modular design separating core logic from UI:

- **Entry Point**: `main.py` initializes the `QApplication`, `MainWindow`, and connects core components.
- **Core Logic (`core/`)**:
  - `receiver.py`: Handles UDP data reception in a separate thread.
  - `parser.py`: Parses raw UDP packets into structured data frames.
  - `recorder.py`: Manages raw data recording to `.bin` files.
  - `playback.py`: Handles replaying of recorded `.bin` files.
  - `reconstructor.py`: Implements offline data reconstruction algorithms (Peak, Matched Filter).
- **User Interface (`ui/`)**:
  - `mainwindow.ui`: The layout definition (Qt Designer file).
  - Loaded dynamically in `main.py` using `uic.loadUi("ui/mainwindow.ui", self)`.
- **Visualization**:
  - Uses `pyqtgraph` for high-performance real-time plotting.
  - Custom `ImageItem` for heatmaps and `PlotItem` for histograms.

## Data Flow & Threading

1.  **Data Ingestion**: `UdpReceiver` runs in a background thread, receiving packets and reassembling frames.
2.  **Signal Emission**: Upon completing a frame, `UdpReceiver` emits PyQt signals (`sig_update_int_rng`, `sig_update_tof`) carrying `numpy` arrays.
3.  **UI Update**: The main thread receives signals and updates `pyqtgraph` widgets. This ensures thread safety for UI operations.

## Key Conventions

- **UI Files**: Do not convert `.ui` files to Python code manually. Use `uic.loadUi` to load them at runtime.
- **Data Structures**:
  - Image data is handled as `numpy` arrays (typically 128x128).
  - ToF data is `uint16` (0-16000), Intensity/Range is typically 8-bit or scaled.
- **UDP Protocol**:
  - **Header**: 0xAA55
  - **Payload**: 4096 bytes per packet.
  - **Frame Assembly**:
    - Type 0 (Intensity/Range): 16 packets/frame (64KB).
    - Type 1 (ToF): 8 packets/frame (32KB).
- **Coordinate System**: `pyqtgraph`'s `ImageItem` often requires transposing data (`.T`) to match standard image coordinates (width x height vs row x col).

## Configuration

- Global constants are defined in `config.py`.
- Theme settings are in `utils/theme.py`.
