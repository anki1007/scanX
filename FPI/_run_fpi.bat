
@echo off
cd /d E:\FPI
if exist _run_done.flag del _run_done.flag
echo === RUN START === > _last_run.txt
python fpi_update.py >> _last_run.txt 2>&1
set ERR1=%errorlevel%
echo. >> _last_run.txt
echo === PYTHON EXIT CODE: %ERR1% === >> _last_run.txt
echo. >> _last_run.txt
echo === FPI_DATA.JSON STRUCTURE INSPECTION === >> _last_run.txt
python -c "import json; d=json.load(open('fpi_data.json')); print('top type:', type(d).__name__); print('top keys:', list(d.keys()) if isinstance(d, dict) else 'list len='+str(len(d)))" >> _last_run.txt 2>&1
echo. >> _last_run.txt
echo === DONE === >> _last_run.txt
echo DONE > _run_done.flag