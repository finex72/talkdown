@echo off
python -m pip install -r requirements-dev.txt
python -m PyInstaller --onefile --noconsole --clean --name SpotifyPTTDucker --collect-submodules pycaw --hidden-import comtypes.stream spotify_ptt_ducker.py
echo.
echo Fertig: dist\SpotifyPTTDucker.exe
