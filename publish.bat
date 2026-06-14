@echo off
rem One-click publish: refresh PEAD data, commit everything, push to GitHub
rem Pages, then exit. (Equivalent to: scanX.bat -Publish)
call "%~dp0scanX.bat" -Publish
