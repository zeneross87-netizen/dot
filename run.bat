@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 이 설치되어 있지 않습니다. https://www.python.org/downloads/ 에서 설치하세요.
  echo 설치 시 "Add python.exe to PATH" 를 꼭 체크하세요.
  pause
  exit /b 1
)
if not exist .venv (
  echo 처음 실행: 필요한 라이브러리를 설치합니다...
  python -m venv .venv
  call .venv\Scripts\python -m pip install --upgrade pip
  call .venv\Scripts\python -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw.exe dotvideo_app.py
