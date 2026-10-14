@echo off
title Jail Management System Launcher
echo ========================================================
echo       JAIL MANAGEMENT SYSTEM (JMS) WEB LAUNCHER
echo ========================================================
echo.
echo Checking database connection...
python -c "from pymongo import MongoClient; client=MongoClient('mongodb://localhost:27017/', serverSelectionTimeoutMS=2000); client.admin.command('ping')" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Could not connect to local MongoDB database.
    echo Please make sure MongoDB service is active.
    echo.
    pause
    exit /b
)

echo [OK] MongoDB database connected successfully.
echo.
echo Detecting Network IP for Multi-Device Access...
for /f "tokens=*" %%a in ('python -c "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('8.8.8.8', 80)); print(s.getsockname()[0]); s.close()" 2^>nul') do set LOCAL_IP=%%a

if "%LOCAL_IP%"=="" (
    set LOCAL_IP=127.0.0.1
)

echo.
echo ========================================================
echo   ACCESS JMS ON ANY DEVICE:
echo   ------------------------------------------------------
echo   1. On this computer:
echo      http://localhost:5000
echo.
echo   2. On Mobile / Tablet / Laptop (Same Wi-Fi / LAN):
echo      http://%LOCAL_IP%:5000
echo ========================================================
echo.
echo Starting Flask web server...
start "" "http://localhost:5000"
python server.py
pause
