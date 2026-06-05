@echo off
REM ─── EXE Icon Extractor — Build script ───────────────────────────────────────
REM Requer: conda activate icon-extractor

echo [BUILD] A instalar PyInstaller...
pip install pyinstaller

echo [BUILD] A compilar...
pyinstaller ^
  --onefile ^
  --windowed ^
  --name "EXEIconExtractor" ^
  --icon "assets\icon.ico" ^
  --add-data "assets;assets" ^
  --hidden-import "PIL._tkinter_finder" ^
  --hidden-import "customtkinter" ^
  --hidden-import "icoextract" ^
  --hidden-import "lief" ^
  --hidden-import "pefile" ^
  --collect-all "customtkinter" ^
  exe_icon_extractor.py

echo.
if exist "dist\EXEIconExtractor.exe" (
    echo [OK] Compilado com sucesso: dist\EXEIconExtractor.exe
) else (
    echo [ERRO] Compilacao falhou. Ver output acima.
)
pause
