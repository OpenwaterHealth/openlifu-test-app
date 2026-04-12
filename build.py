#!/usr/bin/env python3
"""
Build script for OpenLIFU Test Application using PyInstaller
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

def clean_build():
    """Clean previous build artifacts"""
    print("🧹 Cleaning previous build artifacts...")
    dirs_to_clean = ['build', 'dist', '__pycache__']
    
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)
            print(f"   Removed {dir_name}/")
    
    # Clean .spec files
    for spec_file in Path('.').glob('*.spec'):
        spec_file.unlink()
        print(f"   Removed {spec_file}")

def build_app():
    """Build the application using PyInstaller"""
    print("🔨 Building OpenLIFU Test Application...")
    
    # Check if we're in a virtual environment
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        print("✅ Virtual environment detected")
    else:
        print("⚠️  Warning: Not in a virtual environment. Consider using one.")
    
    # PyInstaller command
    cmd = [
        'pyinstaller',
        '--onedir',                    # Create a one-folder bundle
        '--windowed',                  # Don't show console window (for GUI apps)
        '--name=OpenLIFU-TestApp',     # Name of the executable
        '--icon=assets/images/icon.ico' if os.path.exists('assets/images/icon.ico') else '',
        '--add-data=assets;assets',    # Include assets folder
        '--add-data=components;components',  # Include QML components
        '--add-data=pages;pages',      # Include QML pages
        '--add-data=preset_templates;preset_templates',  # Include seed preset JSON files
        '--add-data=*.qml;.',          # Include QML files in root
        '--add-data=*.json;.',         # Include JSON files
        '--hidden-import=PyQt6.QtCore',
        '--hidden-import=PyQt6.QtGui',
        '--hidden-import=PyQt6.QtWidgets',
        '--hidden-import=PyQt6.QtQml',
        '--hidden-import=PyQt6.QtQuick',
        '--hidden-import=matplotlib.backends.backend_qt5agg',
        '--collect-submodules=sounddevice',
        '--collect-submodules=soundfile',
        'main.py'
    ]
    
    # Remove empty icon parameter if no icon exists
    cmd = [arg for arg in cmd if arg]
    
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        print("✅ Build successful!")
        print(f"📦 Executable created in: dist/OpenLIFU-TestApp/")
        return True
    else:
        print("❌ Build failed!")
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        return False

def create_launcher_script():
    """Create a simple launcher script"""
    launcher_content = '''@echo off
echo Starting OpenLIFU Test Application...
cd /d "%~dp0"
OpenLIFU-TestApp.exe
pause
'''
    
    dist_path = Path('dist/OpenLIFU-TestApp/')
    if dist_path.exists():
        launcher_path = dist_path / 'launch.bat'
        with open(launcher_path, 'w') as f:
            f.write(launcher_content)
        print(f"✅ Created launcher script: {launcher_path}")

def main():
    """Main build process"""
    print("🚀 OpenLIFU Test Application Build Script")
    print("=" * 50)
    
    # Check if PyInstaller is available
    try:
        subprocess.run(['pyinstaller', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ PyInstaller not found. Install with:")
        print("   pip install pyinstaller")
        print("   or")
        print("   pip install -e .[dev]")
        sys.exit(1)
    
    # Clean previous builds
    clean_build()
    
    # Build the application
    if build_app():
        create_launcher_script()
        print("\n🎉 Build completed successfully!")
        print("📁 Check the dist/OpenLIFU-TestApp/ folder for your executable")
    else:
        print("\n💥 Build failed! Check the error messages above.")
        sys.exit(1)

if __name__ == '__main__':
    main()