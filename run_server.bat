 .@echo off
title SecurePlate Web Server
call .venv\Scripts\activate.bat
echo.
echo  Installing/checking Flask...
pip install flask --quiet
echo.
echo  Starting SecurePlate Web Server...
echo  Open http://localhost:5000 in your browser
echo.
python server.py
pause
