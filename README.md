# Spotify PTT Ducker

Automatically lowers Spotify's volume while you talk – and fades it back in afterwards.
Built for flight simulation (VATSIM / vPilot) but works for any voice chat.

*Deutsch: Senkt die Spotify-Lautstärke, solange du die Push-to-Talk-Taste hältst oder in
vPilot Funkverkehr zu hören ist, und blendet sie danach sanft wieder ein.*

## Features

- **Push-to-talk ducking** – hold your PTT button (joystick button **or** keyboard key), Spotify drops quickly; release it and Spotify fades back in.
- **Joystick support** – works with any DirectInput/HID joystick, throttle or button box.
- **Audio watching** – also ducks when another program (default: `vPilot.exe`) plays sound, e.g. incoming ATC calls. Add more programs in the settings.
- **First-run setup** – on first launch you just press your PTT button; no config files.
- **Settings window** – duck level, fade times, watched programs, sensitivity, autostart with Windows.
- Only Spotify's own volume slider (in the Windows mixer) is changed – not your system volume.
- Your original Spotify volume is restored exactly, also when you close the program.

## Download & use

1. Download `SpotifyPTTDucker.exe` from the [Releases](../../releases) page.
2. Start it. On first launch, press the button you want to use as push-to-talk.
3. Play something in the **Spotify desktop app** and talk.

Settings are stored in `%APPDATA%\SpotifyPTTDucker\config.json`.

> **Windows SmartScreen / antivirus:** the exe is not code-signed, so Windows may show
> "Windows protected your PC" – click *More info → Run anyway*. Some antivirus tools
> flag programs that listen for global key presses (this one does, to detect your PTT key)
> or that are packed with PyInstaller. The full source is in this repository, and every
> release lists a SHA-256 checksum so you can verify the download. You can also build the
> exe yourself (see below).

## Requirements

- Windows 10/11 (64-bit)
- Spotify **desktop app** (the web player does not get its own entry in the Windows mixer)

## Run / build from source

```
pip install -r requirements.txt
python spotify_ptt_ducker.py
```

Build the single-file exe: run `build.bat` (needs `pip install -r requirements-dev.txt`).

`tools/find_joystick_button.py` prints the number of any joystick button you press (for debugging).

## Limitations

- Two joysticks with an identical name cannot be told apart.
- Key combinations (e.g. `Ctrl+Space`) are not supported as PTT, only single keys/buttons.
- Mouse buttons are not supported.
- If a game blocks global key hooks, run the program as administrator (keyboard PTT only; joystick buttons are not affected).

## Disclaimer

Not affiliated with or endorsed by Spotify AB or the developers of vPilot / VATSIM.
All trademarks belong to their respective owners. Provided "as is" under the MIT license.
