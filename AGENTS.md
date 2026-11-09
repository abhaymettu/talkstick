# AGENTS.md

Notes for coding agents working in this repo.

## Layout
- `daemon/talkstick`: the whole daemon, one stdlib Python 3.9+ file. Engines, delivery and the M5R1 server live here.
- `tests/test_talkstick.py`: stdlib unittest. Run `python3 -m unittest discover -s tests` before and after any daemon change.
- `firmware/talkstick/app.cpp`: the M5StickS3 firmware (Arduino on ESP-IDF via the `m5stack:esp32` core, M5Unified).
  `talkstick.ino` is a stub so Arduino's prototype generation leaves app.cpp alone.
- `firmware/build.sh`, `firmware/flash.sh`, `firmware/tools/`: build from a private env file, flash, bench over serial, screenshots.
- `docs/protocol.md`: the wire protocol. Change it together with both sides.
- `examples/`: delivery targets (webhook echo, T3 Code thread). New targets go here, not into the daemon.

## Rules
- Secrets (WiFi password, tokens) never enter the repo. `build.sh` writes the generated header and the image to
  folders outside the repo, mode 700. Do not print config values in logs.
- Keep the daemon dependency-free. If a feature needs a library, make it an engine or delivery `command` instead.
- Firmware screen changes: every visible element needs a one-line job in `firmware/README-firmware.md`; states must
  read by word or shape, not colour alone.
- Hardware checks: the serial console (115200) has `ptt 3`, `pcm`, `press a|b`, `state`, `shot`; use them to verify on
  a real stick instead of assuming. `daemon/talkstick fake-stick file.wav host` exercises the daemon without hardware.

## Build and test
```sh
python3 -m unittest discover -s tests
daemon/talkstick --dry serve &   # then: daemon/talkstick fake-stick some.wav localhost
firmware/build.sh && firmware/flash.sh
```
