@echo off
:: Capital Surabaya — Local Price Helper
:: Double-click this to start. Leave the window open while using the dashboard.
:: The "Update Prices" button on the Watchlist page talks to this helper.

cd /d "%~dp0"
title Capital Surabaya Helper
echo Starting Capital Surabaya price helper...
echo.
python helper_server.py
pause
