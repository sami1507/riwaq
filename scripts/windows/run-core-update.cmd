@echo off
cd /d "%~dp0"
rem -vvv keeps the full assertion detail in pytest-after.log, so an intermittent failure shows its reason.
set PYTEST_ADDOPTS=-vvv
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-and-verify.ps1" -SkipBaselineTests
