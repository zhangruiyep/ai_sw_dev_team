@echo off
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set "PYTHONUTF8=1"
python -m ai_sw_dev_team %*
endlocal
