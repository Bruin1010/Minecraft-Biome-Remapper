@echo off
setlocal

REM Build a single-file Windows executable (GUI) using PyInstaller.
REM Prereqs:
REM   python -m pip install -r requirements.txt
REM   python -m pip install pyinstaller

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo.
  echo ERROR: PyInstaller is not installed for this Python.
  echo Install it with:
  echo   python -m pip install pyinstaller
  echo.
  exit /b 1
)

if not exist "app.ico" (
  echo.
  echo ERROR: app.ico not found.
  echo This project embeds the icon into the EXE so end users only need the .exe.
  echo To build, place an ICO file named app.ico next to build_exe.bat, then rerun.
  echo.
  exit /b 3
)

python -m PyInstaller --noconfirm --clean --onefile --windowed --icon app.ico ^
  --name "TerralithBiomeRemapper" ^
  terralith_biome_remap_gui.py

if exist "dist\TerralithBiomeRemapper.exe" (
  REM Copy default mapping.ini next to the exe so users can edit it.
  if exist "mapping.ini" (
    copy /Y "mapping.ini" "dist\mapping.ini" >nul
  ) else (
    echo WARNING: mapping.ini not found next to build_exe.bat; EXE will require a mapping.ini to run.
  )
  echo.
  echo Done. Look in: dist\TerralithBiomeRemapper.exe
  exit /b 0
) else (
  echo.
  echo ERROR: Build failed. No exe was produced.
  exit /b 2
)
endlocal


