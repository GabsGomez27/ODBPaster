@echo off
setlocal
pushd "%~dp0"

if not exist "config.json" goto config_missing

python -m PyInstaller --version >nul 2>nul
if errorlevel 1 goto pyinstaller_missing

python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onedir ^
    --noconsole ^
    --name "ODB Paster" ^
    --specpath build ^
    --hidden-import webpaster ^
    --collect-all playwright ^
    main.py
if errorlevel 1 goto build_failed

copy /Y "config.json" "dist\ODB Paster\config.json" >nul
if errorlevel 1 goto config_copy_failed

echo Build completed. Edit dist\ODB Paster\config.json to change the connection.
popd
exit /b 0

:config_missing
echo config.json was not found beside build.bat.
popd
exit /b 1

:pyinstaller_missing
echo PyInstaller is not installed. Run: python -m pip install -r requirements-build.txt
popd
exit /b 1

:build_failed
echo PyInstaller build failed.
popd
exit /b 1

:config_copy_failed
echo The executable was built, but config.json could not be copied to the output folder.
popd
exit /b 1
