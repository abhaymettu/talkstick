# talkstick firmware (M5StickS3)

Push-to-talk for an M5StickS3 (ESP32-S3, ES8311 mic). Hold the front button, speak, let go. The stick streams
16 kHz mono PCM to the Mac daemon over TCP (protocol M5R1, see the protocol notes) and shows the daemon's progress
until the reply lands on screen.

## Hardware used

| Part | Pin or bus | Use |
|---|---|---|
| KEY1, front button A | G11 (RTC GPIO, active low) | talk, status request, deep-sleep wake |
| KEY2, side button B | G12 (active low) | cancel while talking, read the last reply |
| ES8311 codec | I2C 0x18 (SCL G48, SDA G47), I2S MCLK G18, BCLK G17, LRCK G15, DIN G16, DOUT G14 | mic, quiet clicks |
| ST7789P3 display | 135x240, used landscape | screens below |
| M5PM1 power chip | I2C | battery %, USB (VBUS) detection |

Mic and speaker share the codec and never run together: clicks play only while the mic is off.

## Buttons

| Action | Result |
|---|---|
| Hold A | listen; release sends the audio (`E`) |
| Tap A (under 300 ms) | status request (`S`); the daemon answers through the same reply path |
| B while listening | cancel (`C`) |
| B otherwise | show the last reply; each press turns the page; back to the clock after 20 s |

## Screens and what each element is for

Near-black background, one typeface (FreeSans, sizes 9, 18 bold, 24 bold), one accent colour per screen from the
Okabe-Ito set. Every screen is named by its word or its shape, so the colour never carries meaning alone.

| Screen | Element | Its job |
|---|---|---|
| Idle | clock HH:MM | tells the time (from the daemon's `TIME`; `--:--` until the first contact) |
| Idle | battery outline, fill and % (plus `usb` when plugged in) | says how much charge is left and whether it will sleep |
| Idle | four WiFi bars, or `no wifi` | says how strong the WiFi link is |
| Idle | `Mac` or `no Mac` | says whether a message would reach the Mac right now |
| Listening | `Listening` in orange | says the mic is recording |
| Listening | level bar | shows the mic is hearing you |
| Listening | seconds held, `ends in N s` from 100 s | warns before the 120 s cap |
| Sent | `Sent` in sky blue | says the audio left the stick |
| Sent | sub-label: sending, received, waiting N s, finding the Mac, status request | says which step the daemon is on |
| Thinking | `Thinking` in sky blue with three pulsing dots | says the driver is working and the stick has not frozen |
| Thinking | elapsed m:ss | says how long the reply is taking |
| Replied | paw print in bluish green | says a reply arrived |
| Replied, Reading | reply text, four lines per page | the reply itself |
| Replied, Reading | page `2/4` | says how far through a long reply you are |
| Problem | `Offline`, `Error`, `No reply`, `No link`, `Too long` in vermillion | says what went wrong |
| Problem | reason line | says why, in a few words |
| Cancelled | `Cancelled` in grey | confirms button B dropped the recording |

Clicks, all quiet: listening is one short high tick, sent is two ticks, replied is a rising pair, a problem is one
low tone. `sound off` on the serial console turns them off (kept across sleep).

## Behaviour

- Audio is buffered in PSRAM from the moment button A goes down (including the press that woke the stick), so speech
  said while WiFi is still joining is sent first once the socket opens.
- Discovery order: cached IP:port from the last good connect (RTC memory, kept across deep sleep, 1 s connect
  timeout), then the Bonjour service, then the fallback host name. The WiFi channel and BSSID are cached too, so a
  wake rejoins without a scan.
- On each wake the stick pings the Mac (`P`) for the clock (`TIME`) and the last reply (`LAST`), then closes. On USB
  it pings again every 5 minutes.
- After `E` or `S` the socket stays open for `REPLIED`, `OFFLINE` or `ERR` up to 11 minutes. `REPLY <text>` before
  `REPLIED` is kept in RTC memory (up to 720 characters), so button B can show it after a deep sleep.
- On battery: a reply stays on screen 5 s plus 40 ms per character (at most 20 s), a problem 5 s, then the stick
  deep-sleeps; otherwise it sleeps after 60 s idle. Button A wakes it.
- On USB power: it never sleeps, and the screen dims after 10 s idle, so the serial console stays usable.

## Build and flash

Needs `arduino-cli` with the M5Stack board index (`m5stack:esp32` core 3.3.10, board `m5stack:esp32:m5stack_sticks3`),
the M5Unified (>= 0.2.12, built with 0.3.0), M5GFX (0.3.0) and M5PM1 (1.0.7) libraries, and `esptool`.

```sh
mkdir -p ~/.config/talkstick
cp firmware/config.example.env ~/.config/talkstick/firmware.env   # fill in WiFi, service, port, fallback host
chmod 600 ~/.config/talkstick/firmware.env
firmware/build.sh                        # or: firmware/build.sh a.env b.env (later files win)
firmware/flash.sh                        # PORT=/dev/cu.usbmodemXXXX to pick the port
```

`build.sh` turns the config into a header in `TALKSTICK_GEN_DIR` (default `~/.config/talkstick/gen`) and builds into
`TALKSTICK_BUILD_DIR` (default `~/.cache/talkstick/build`). Both directories are created mode 700 because the header
and the image contain the WiFi password. `TALKSTICK_*` environment variables override the config files.

If flashing cannot reach the bootloader, hold the power button until the green LED blinks (ROM download mode) and
run `flash.sh` again.

## Serial console and bench tools

115200 baud on the USB port. The stick logs boot, WiFi, discovery, screens and byte counts.

| Command | Effect |
|---|---|
| `ptt 3` | act as if button A were held for 3 s (the mic records for real) |
| `press a`, `press b` | tap button A (status request) or press button B |
| `pcm <bytes>` | answers `pcm ready`, then takes that many bytes of 16 kHz mono int16 PCM and sends them as an utterance |
| `shot` | dumps the screen as `SHOT <w> <h> RGB565BE <bytes>`, the pixels, `SHOT END` |
| `state` | prints the current screen and phase |
| `status` | version, phase, WiFi, target, battery, VBUS, G11 level, clock |
| `ping` | pings the Mac now |
| `gpio` | G11 level with and without the internal pull-up |
| `sound on`, `sound off` | clicks on or off |
| `sleep` | deep-sleep now (wake with button A) |
| `forget` | clear the cached host and WiFi channel |
| `reboot` | restart |

Host side (pyserial, e.g. `uv run --with pyserial python3 ...`):

- `firmware/tools/bench.py cmd "ptt 3"` sends a command and prints the log.
- `firmware/tools/bench.py pcm speech.wav` pushes a recording through the stick's WiFi path, for round trips with
  real speech when nobody is at the stick.
- `firmware/tools/screenshot.py out.png` saves the screen as a PNG, scaled 2x.
