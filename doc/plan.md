# Serial UI Refactoring Plan

## Problem Statement
The user wants to refine the Serial Control interface:
1.  **Port/Baud Selection**: Both must be Dropdowns (`QComboBox`). (Already true, but ensure Baud has options).
2.  **Layout**: Strict vertical ordering from **0xC1 to 0xCA**.
    -   **Left Column**: Command Trigger Button (e.g., "Read ID", "Set Temp").
    -   **Right Column**: Configuration parameters (SpinBoxes, Checkboxes) or Status Labels.

## Proposed Approach
1.  **Modify `ui/mainwindow.ui`**:
    -   **Baud Rate**: Populate `combo_baud` with standard rates (9600...921600).
    -   **Command Layout**: Redesign `gridLayout_serial_cmds` to be a 2-column layout (Action | Config).
    -   **Ordering**:
        1.  **0xC1 (ID)**: [Btn: 读取组件编号] | [Label: ID Display]
        2.  **0xC2 (Cooler On)**: [Btn: 制冷机上电] | -
        3.  **0xC3 (Temp)**: [Btn: 设置温度] | [SpinBox: Temp]
        4.  **0xC4 (APD Cfg)**: [Btn: APD配置] | [Chk: Trig] [Chk: TestPoint] [Chk: TestMode]
        5.  **0xC5 (Algo)**: [Btn: 算法配置] | [SpinBox: Frame] [SpinBox: Noise] [SpinBox: Step] [SpinBox: Thresh]
        6.  **0xC6 (Proj)**: [Btn: 发送弹体信息] | [SpinBox: Dist] [SpinBox: Vel]
        7.  **0xC7 (Detector On)**: [Btn: 探测器上电] | -
        8.  **0xC8 (Detector Off)**: [Btn: 探测器下电] | -
        9.  **0xC9 (Cooler Off)**: [Btn: 制冷机下电] | -
        10. **0xCA (Bias)**: [Btn: 设置偏压] | [SpinBox: Bias]
    -   This is strictly "C1 to CA" as requested.

2.  **Implementation**:
    -   Edit `ui/mainwindow.ui` to rebuild `gridLayout_serial_cmds`.
    -   Add `item`s to `combo_baud`.

## Todo List
- [ ] Update `ui/mainwindow.ui`:
    -   Populate `combo_baud`.
    -   Rebuild `gridLayout_serial_cmds` with rows for C1, C2, C3, C4, C5, C6, C7, C8, C9, CA.
    -   Ensure "Left = Button, Right = Config" layout.
- [ ] Verify `main.py` connections (names should match).
