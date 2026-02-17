@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
color 0A
set PYTHON=python

:MENU
cls
echo ================================
echo    Python Project Launcher 🚀
echo ================================
echo   [1] Environment Tasks
echo   [2] Pip Package Tasks
echo   [3] Requirements Management
echo   [4] Bonus Tools
echo   [5] Open Console
echo   [6] Exit
echo   [?] Help
echo ================================
set /p choice= Select a section: 

if "%choice%"=="1" goto ENV_TASKS
if "%choice%"=="2" goto PIP_TASKS
if "%choice%"=="3" goto REQ_TASKS
if "%choice%"=="4" goto BONUS_TASKS
if "%choice%"=="5" goto CONSOLE
if "%choice%"=="6" goto END
if "%choice%"=="?" goto HELP

echo Invalid choice. Try again.
pause >nul
goto MENU

:HELP
cls
echo --- Help ---
echo [1] Environment Tasks: Create, activate, or delete virtual environments.
echo [2] Pip Package Tasks: Install or uninstall Python packages.
echo [3] Requirements Management: Save or restore package lists.
echo [4] Bonus Tools: Extra utilities (linting, formatting, etc.).
echo [5] Open Console: Drop into an interactive cmd prompt in this directory.
echo [6] Exit: Quit this launcher.
echo -------------------
pause
goto MENU

:ENV_TASKS
cls
echo --- Environment Tasks ---
echo [1] Create virtual env
echo [2] Activate venv
echo [3] Delete venv
echo [B] Back
set /p sub= Choose action: 

if "%sub%"=="1" (
    %PYTHON% -m venv venv
    if errorlevel 1 (color 0C & echo Failed to create venv. & color 0A) else (echo venv created successfully.)
    pause
    goto ENV_TASKS
)
if "%sub%"=="2" (
    call venv\Scripts\activate
    if errorlevel 1 (color 0C & echo Failed to activate venv. & color 0A)
    goto ENV_TASKS
)
if "%sub%"=="3" (
    rmdir /s /q venv
    if errorlevel 1 (color 0C & echo Failed to delete venv. & color 0A) else (echo venv deleted.)
    pause
    goto ENV_TASKS
)
if /I "%sub%"=="B" goto MENU

echo Invalid choice.
pause
goto ENV_TASKS

:PIP_TASKS
cls
echo --- Pip Package Tasks ---
echo [1] Install package
echo [2] Uninstall package
echo [3] List installed packages
echo [B] Back
set /p sub= Choose action: 

if "%sub%"=="1" (
    set /p pkg= Package name: 
    pip install %pkg%
    pause
    goto PIP_TASKS
)
if "%sub%"=="2" (
    set /p pkg= Package name: 
    pip uninstall %pkg%
    pause
    goto PIP_TASKS
)
if "%sub%"=="3" (
    pip list
    pause
    goto PIP_TASKS
)
if /I "%sub%"=="B" goto MENU

echo Invalid choice.
pause
goto PIP_TASKS

:REQ_TASKS
cls
echo --- Requirements Management ---
echo [1] Freeze to requirements.txt   (exact recreatable)
echo [2] Install from requirements.txt
echo [3] Save pip list to installed_packages.txt (for reference)
echo [4] Restore from installed_packages.txt
echo [B] Back
set /p sub= Choose action: 

if "%sub%"=="1" (
    pip freeze > requirements.txt
    echo Exact versions saved to requirements.txt
    pause
    goto REQ_TASKS
)
if "%sub%"=="2" (
    pip install -r requirements.txt
    pause
    goto REQ_TASKS
)
if "%sub%"=="3" (
    pip list > installed_packages.txt
    echo Package list saved to installed_packages.txt
    pause
    goto REQ_TASKS
)
if "%sub%"=="4" (
    if not exist installed_packages.txt (
        color 0C
        echo installed_packages.txt not found!
        color 0A
        pause
        goto REQ_TASKS
    )
    echo Converting installed_packages.txt to temp_requirements.txt...
    > temp_requirements.txt (
        for /f "skip=2 tokens=1,2" %%a in (installed_packages.txt) do (
            echo %%a==%%b
        )
    )
    pip install -r temp_requirements.txt
    del temp_requirements.txt
    pause
    goto REQ_TASKS
)
if /I "%sub%"=="B" goto MENU

echo Invalid choice.
pause
goto REQ_TASKS

:BONUS_TASKS
cls
echo --- Bonus Tools ---
echo [1] Lint with flake8
echo [2] Format with black
echo [B] Back
set /p sub= Choose action: 

if "%sub%"=="1" (
    pip show flake8 >nul 2>&1 || pip install flake8
    flake8 .
    pause
    goto BONUS_TASKS
)
if "%sub%"=="2" (
    pip show black >nul 2>&1 || pip install black
    black .
    pause
    goto BONUS_TASKS
)
if /I "%sub%"=="B" goto MENU

echo Invalid choice.
pause
goto BONUS_TASKS

:CONSOLE
cls
echo Dropping into console... type 'exit' to return.
cmd /k
goto MENU

:END
echo Exiting...
timeout /t 1 >nul
exit
