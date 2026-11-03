@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  call .venv\Scripts\python -m pip install --upgrade pip
  call .venv\Scripts\python -m pip install -r requirements.txt
)
call .venv\Scripts\python -m pip install pyinstaller
call .venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed ^
  --name DotVideoConverter ^
  --collect-all imageio_ffmpeg ^
  dotvideo_app.py
echo.
echo 완료: dist\DotVideoConverter.exe
pause
