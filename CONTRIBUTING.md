# Contributing

Issues and pull requests are welcome. Keep changes small and say what you tested: for firmware, the board and the serial lines you saw (`ptt 3` on the serial console is the hands-free bench); for the daemon, `python3 -m unittest discover tests` plus a `talkstick fake-stick` round trip. New transcription engines and delivery targets belong in the config as a `command` or `webhook`, or in `examples/`, rather than as special cases in the daemon. Never commit WiFi credentials, tokens or personal paths; `firmware.env` and `config.json` live outside the repo.
