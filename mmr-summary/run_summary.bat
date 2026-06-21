@echo off
:: FIRE Capital - MMR Summary Runner
:: Usage: run_summary "ERA_MMR_-_06_15_26.xlsx"

setlocal

if "%~1"=="" (
    echo Usage: run_summary ^<filename^>
    echo Example: run_summary "ERA_MMR_-_06_15_26.xlsx"
    exit /b 1
)

:: Resolve the file path - support both bare filenames and full paths
set "FILE=%~1"
if not exist "%FILE%" (
    :: Try looking in the same folder as this batch file
    set "FILE=%~dp0%~1"
)

if not exist "%FILE%" (
    echo Error: File not found: %~1
    exit /b 1
)

python "%~dp0generate_summary.py" "%FILE%"
endlocal
