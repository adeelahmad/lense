@echo off
rem Double-click to install Lens on Windows: opens Lens's installer in your browser (install.ps1).
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "irm https://raw.githubusercontent.com/adeelahmad/lense/main/install.ps1 | iex"
