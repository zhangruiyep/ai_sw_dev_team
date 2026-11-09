@echo off
rem Compatibility entry: forwards to ai_sw_dev_team.cmd for scripts that still call "team ...".
call "%~dp0ai_sw_dev_team.cmd" %*
