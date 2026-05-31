@echo off
:: Capital Surabaya — Pre-Market Run (15:00 ICT = 08:00 UTC)
:: Runs before US market open. Execution intents sent to paper ledger.

cd /d "C:\Users\kanna\OneDrive - The Siam Cement Public Company Limited\Desktop\Capital Surabaya - Claude Code"

echo [%DATE% %TIME%] Starting pre_market pipeline...

python -m pipeline.runner --run-type pre_market >> logs\scheduler.log 2>&1

echo [%DATE% %TIME%] pre_market pipeline finished (exit code: %ERRORLEVEL%)
