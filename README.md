# OpenLIFU Test Application

Python example UI for OPEN LIFU used for Hardware Testing and Basic Usage

![App Image](docs/app_image.png)

## Installation

### Prerequisites
- **Python 3.10 or later**: Make sure you have Python 3.10 or later installed on your system. You can download it from the [official Python website](https://www.python.org/downloads/).

### Steps to Set Up the Project
1. **Clone the repository**:
   ```bash
   git clone https://github.com/OpenwaterHealth/OpenLIFU-TestAPP.git
   cd OpenLIFU-TestAPP
   ```

2. **Create and activate a virtual environment** (recommended):
   ```bash
   python -m venv .venv
   
   # On Windows:
   .venv\Scripts\activate
   
   # On macOS/Linux:
   source .venv/bin/activate
   ```

3. **Install the application with dependencies**:
   ```bash
   # Install base dependencies
   pip install -e .
   
   # Or install with development tools
   pip install -e .[dev]
   
   # Or install with test dependencies
   pip install -e .[test]
   
   # Or install everything
   pip install -e .[dev,test]
   ```

4. **Install OpenLIFU Python**:
   ```bash
   git clone https://github.com/OpenwaterHealth/OpenLIFU-python.git
   cd OpenLIFU-python
   pip install -e .
   cd ..
   ```

5. **Run the application**:
   ```bash
   python main.py
   ```

## Demo Input Units

The Demo page displays a few timing and frequency fields in operator-friendly units. Internally, solution data and device configuration still use SI base units.

- **Frequency** in the UI is shown in **kHz**.
   - Example: `400` in the UI is stored/sent as `400000` Hz.
- **Duration** in the UI is shown in **uS**.
   - Example: `200` in the UI is stored/sent as `0.0002` s.
- **Pulse Interval** in the UI is shown in **ms**.
   - Example: `100` in the UI is stored/sent as `0.1` s.

Additional notes:
- **Pulse Train Interval** remains in seconds in the UI.
- Loading a solution from JSON converts SI values back to UI units for these fields.
- Sending a solution to configuration/plot paths converts those displayed values back to SI units.

## Building Executable

### Quick Build (Windows)
Simply double-click `build.bat` or run it from command prompt:
```cmd
build.bat
```

### Manual Build
1. **Install development dependencies**:
   ```bash
   pip install -e .[dev]
   ```

2. **Run the build script**:
   ```bash
   python build.py
   ```

3. **Find your executable**:
   - Executable location: `dist/OpenLIFU-TestApp/`
   - Use the launcher: `dist/OpenLIFU-TestApp/launch.bat`

### Build Options
The build script automatically:
- Cleans previous builds
- Creates a one-folder distribution
- Includes all QML files and assets  
- Creates a Windows launcher script
- Handles PyQt6 and other dependencies