@echo off
python -m pip install -r requirements-dev.txt
python -m PyInstaller --onefile --noconsole --clean --name TalkDown --collect-submodules pycaw --hidden-import comtypes.stream talkdown.py
echo.
echo Fertig: dist\TalkDown.exe
