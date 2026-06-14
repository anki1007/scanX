@echo off
rem One-click CLEAN RESET: wipes the messy git history, snapshots this folder
rem (your local disk = source of truth), checks no credentials are staged,
rem and force-pushes to GitHub. Repo settings, Actions secrets and Pages
rem config are untouched. Close any running scanX window first.
call "%~dp0scanX.bat" -Reset
