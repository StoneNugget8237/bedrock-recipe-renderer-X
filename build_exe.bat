@echo off
rem Rebuild the executables with PyInstaller.
rem
rem Output -- both land in THIS folder, right next to this script:
rem   bedrock-recipe-renderer.exe       GUI (double-click, no Python needed)
rem   bedrock-recipe-renderer-cli.exe   console build (--headless / --sync-vanilla)
rem
rem PyInstaller's own work files (.spec, intermediate builds) are kept under
rem build\pyinstaller and deleted again at the end, so the folder stays clean.
rem
rem This file is ASCII-only on purpose: cmd reads .bat in the OEM codepage.
setlocal
cd /d "%~dp0"

rem PyInstaller needs these data folders to exist; a fresh clone has no caches yet
if not exist "vanilla_cache" mkdir "vanilla_cache"
if not exist "wiki_cache" mkdir "wiki_cache"
if not exist "assets\ui" mkdir "assets\ui"

rem Interpreter that has customtkinter + Pillow + PyInstaller installed.
rem The official installer names the folder Python315, but the arm64 build ships as
rem Python315-arm64 -- both are probed before falling back to whatever "python" is on PATH.
set PY=%LOCALAPPDATA%\Programs\Python\Python315\python.exe
if not exist "%PY%" set PY=%LOCALAPPDATA%\Programs\Python\Python315-arm64\python.exe
if not exist "%PY%" set PY=C:\Python315\python.exe
if not exist "%PY%" set PY=python

rem Stamp both executables with a Windows version resource (Properties > Details).
rem The version number comes from mcrender\__init__.py::__version__ -- never hardcode it here;
rem tools\make_version_file.py reads it and writes build\version_*.txt for --version-file.
"%PY%" tools\make_version_file.py "build\version_gui.txt"
if errorlevel 1 goto fail
"%PY%" tools\make_version_file.py "build\version_cli.txt" --exe "bedrock-recipe-renderer-cli.exe"
if errorlevel 1 goto fail

rem customtkinter ships its themes/assets as data files next to the code, so it needs
rem --collect-all (a plain --hidden-import would drop them and the window would be unstyled).
rem darkdetect backs the light/dark auto-switch; pywinstyles paints the Mica backdrop.
set CTK=--collect-all customtkinter --collect-all darkdetect --collect-all pywinstyles

rem --distpath "." writes the exe straight into this folder (no dist\ subfolder);
rem --workpath / --specpath keep the temporary stuff under build\.
rem
rem  WARNING: the .spec is generated in --specpath, and PyInstaller resolves
rem  --add-data / --icon / --version-file relative to the SPEC's folder -- a bare
rem  "mcrender/glyphs.json" would then be looked up inside build\pyinstaller\.
rem  Everything below is therefore an absolute path (%CD% = this folder).
set WORK=%CD%\build\pyinstaller
if not exist "%WORK%" mkdir "%WORK%"
set PATHS=--distpath "%CD%" --workpath "%WORK%" --specpath "%WORK%"
set DATA=--add-data "%CD%\mcrender\glyphs.json;mcrender" --add-data "%CD%\mcrender\sprites.json;mcrender" --add-data "%CD%\assets;assets" --add-data "%CD%\vanilla_cache;vanilla_cache" --add-data "%CD%\wiki_cache;wiki_cache"
set ICON=--icon "%CD%\assets\icon.ico"

echo [1/2] building GUI exe ...
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "bedrock-recipe-renderer" %ICON% ^
  --version-file "%CD%\build\version_gui.txt" ^
  %PATHS% ^
  %DATA% ^
  %CTK% ^
  --exclude-module matplotlib --exclude-module numpy --exclude-module pandas ^
  mcrender_gui.py
if errorlevel 1 goto fail

echo [2/2] building CLI exe ...
"%PY%" -m PyInstaller --noconfirm --clean --onefile --console ^
  --name "bedrock-recipe-renderer-cli" %ICON% ^
  --version-file "%CD%\build\version_cli.txt" ^
  %PATHS% ^
  %DATA% ^
  %CTK% ^
  --exclude-module matplotlib --exclude-module numpy --exclude-module pandas ^
  mcrender_gui.py
if errorlevel 1 goto fail

rem PyInstaller's scratch dir (and the .spec files) are not needed once the exes exist
if exist "%WORK%" rmdir /s /q "%WORK%"

echo.
echo Done. Built:
echo   "%~dp0bedrock-recipe-renderer.exe"
echo   "%~dp0bedrock-recipe-renderer-cli.exe"
goto :eof

:fail
echo.
echo Build failed.
exit /b 1
