@echo off
:: Capital Surabaya — Post-Close Run (04:00 ICT = 21:00 UTC)
:: Runs after US market close. All 5 agents analyze overnight.

cd /d "C:\Users\kanna\OneDrive - The Siam Cement Public Company Limited\Desktop\Capital Surabaya - Claude Code"

echo [%DATE% %TIME%] Starting post_close pipeline...

python -m pipeline.runner --run-type post_close >> logs\scheduler.log 2>&1

echo [%DATE% %TIME%] post_close pipeline finished (exit code: %ERRORLEVEL%)
