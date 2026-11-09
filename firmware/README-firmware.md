# talkstick firmware (M5StickS3)

Push-to-talk for an M5StickS3 (ESP32-S3, ES8311 mic). Hold the front button, speak, let go. The stick streams
16 kHz mono PCM to the Mac daemon over TCP (protocol M5R1, see the protocol notes) and shows each step until
the words are delivered; replies arrive later on a watch connection.

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
| Hold A | listen; release sends the audio (`E` with the utterance id) |
| Tap A (under 300 ms) | status request (`S`); a second tap while the first is still unanswered (under 90 s) shows `Already asked` and sends nothing |
| B while listening | cancel (`C`) |
| B on a reply | next page; after the last page, the next newer reply; after the newest, home |
| B at home | open the newest stored reply (`No replies yet` when there is none) |
| B on a `demo` screen | leave the demo |

A works again as soon as `E` is written or a send starts retrying, so a new message never waits on an old one. A new
press abandons a pending retry (the recording buffer is reused) and logs it.

## Screens and what each element is for

Background #0C0C0E, one family (Inter, generated into `fonts.h` by `tools/make_fonts.py`: clock 58 px, title 25,
body 16, meta 13, tabular digits). Colours are Okabe-Ito at different lightness: listening yellow, problem orange,
thinking and updating sky, delivered and replied bluish green. Every state is also named in words, so colour never
carries meaning alone. Text that does not fit ends in an ellipsis.

| Screen | Element | Its job |
|---|---|---|
| Idle | clock HH:MM (`--:--` in faint grey until the first `TIME`) | tells the time from the Mac |
| Idle | status line: `Ready`, `Joining WiFi`, `Finding the Mac`, `No WiFi`, `Can't reach the Mac`, `Updated to 0.3.x` | says whether a message would get through right now, and what is wrong if not |
| Idle | `N waiting` (sky, right) | says how many delivered messages still have no answer |
| Idle | meta: `WiFi strong`, `good` or `weak`, battery %, `charging` or `charged` on USB; `Battery low N%` in orange under 15 % | says how healthy the radio and battery are |
| Listening | `Wait` (grey), then `Listening` (yellow) | says whether the mic is already delivering sound |
| Listening | elapsed m:ss (right) | says how long you have held A |
| Listening | 40 level bars | show the mic is hearing you |
| Listening | `B cancels`, then `Ends in N s` (orange) from 100 s | names the way out, then warns before the 120 s cap |
| Sending | `Sending` and one step: `Joining WiFi`, `Finding the Mac`, `Uploading N.N s` (`Asking for status` for a tap), `Transcribing`, `Posting to the thread` | says which step the message is on; nothing claims delivery yet |
| Sending, Delivered, Thinking | `N waiting` (sky, bottom right) | keeps the count of unanswered messages in view |
| Delivered | check mark and `Delivered` (green) | says the daemon answered `SENT`: the words are in the thread |
| Delivered | what was said, in curly quotes, two lines at most | confirms which words went |
| Thinking | `Thinking` (sky), `Queued` (sky) or `Paused` (orange) | says whether the driver is on it, busy with something else, or stopped |
| Thinking | `Driver is on it`, `Driver busy, N ahead`, `Turn stopped, waiting in T3` | says the same in words, with the queue position |
| Thinking | elapsed m:ss since the last delivery (right) | shows the wait is live without an animation |
| Thinking | newest waiting message in quotes (grey) | says which message the wait is for |
| Reply | paw (green) and the said text in quotes | says a reply arrived and what it answers |
| Reply | `2/3` (right, only with more than one page) | says how far through a long reply you are |
| Reply | reply text, five lines per page | the reply itself |
| No reply | `No reply` (orange) and the reason | says an answer came back as offline or error, and why |
| Problem | orange title: `Not sent yet`, `Not sent`, `Didn't catch that`, `Driver offline`, `Couldn't post`, `Transcription failed`, `Too long`, `Something failed` | says what failed |
| Problem | reason, two lines at most | says why, in plain words |
| Problem | next step: `Trying again in N s`, `Will send when the Mac is back`, `Hold A to say it again`, `Hold A a little longer`, `Nothing was posted` | says what happens next |
| Cancelled | `Cancelled` (grey) and `Nothing was sent` | confirms B dropped the recording |
| Notice | `Already asked` or `No replies yet` (grey) | explains why a tap or B did nothing |
| Updating | `Updating` (sky), `to 0.3.1`, progress bar and % | says an update is downloading and how far it is; then `Restarting` |

Which screen shows at rest: Updating, else the oldest unread reply, else Thinking (messages waiting and under 120 s
since the last delivery or 30 s since a button), else Idle. A reply counts as read after 5 s plus 40 ms per
character (at most 20 s) or when B is pressed. Delivered holds 1.8 s, problems 5 s, Cancelled and notices 1.5 s.
`Not sent yet` stays up while the stick retries.

Clicks, all quiet: delivered is two ticks, an answer is a rising pair, a problem is one low tone. `sound off` on the
serial console turns them off (kept across sleep).

## Behaviour

- Audio is buffered in PSRAM from the moment button A goes down (including the press that woke the stick), and
  streamed once the link is up (after 300 ms of hold).
- A network task on core 0 owns WiFi, discovery, the watch connection and updates, so none of it freezes the
  screen. Link states: Joining (WiFi down under 15 s), No WiFi, Searching (WiFi up, watch not confirmed, under 8 s),
  Can't reach the Mac, Up (watch connected and a line in the last 12 s).
- Discovery order: the cached IP:port (RTC memory, 1.5 s timeout), then the Bonjour service, then the fallback host
  name. Failed rounds back off 1, 2, then 3 s, and keep going until the link is up.
- The watch connection (`W`) carries `TIME`, `QUEUE`, `ANSWER` and `OTA`. Each answer is stored (four in RTC
  memory, kept across deep sleep) and acked with `K`. 12 s of silence closes and reconnects it.
- Each utterance has an 8 hex character id. Retryable failures (no link 25 s after release, connect failed, socket
  closed before `SENT`, no `SENT` 45 s after `E`, `ERR busy`) resend the whole recording with the same id, after 2,
  4, then 8 s, for up to 120 s after release. The daemon dedupes by id, so a resend never posts twice.
- Failures nobody saw (connects, watch silence, utterance and update failures) go to a 640 byte diagnostics ring
  that survives sleep and restarts, and is uploaded as `L` on the next watch connection.
- Battery: deep sleep after 60 s idle, except while messages are waiting within 11 minutes of the last delivery,
  while a new image is on probation, or during an update. The radio uses modem power save unless recording,
  sending, or up with messages waiting.
- USB: never sleeps; the screen dims after 10 s at rest, and stays bright on replies, problems and Delivered.

## Over-the-air updates

`build.sh` embeds a 32-byte key from `TALKSTICK_OTA_KEY_FILE` (default `~/.config/talkstick/ota.key`, 64 hex
characters, created with mode 600 if missing). Point the daemon's `ota_key_file` at the same file. A header built
without a key compiles OTA out.

To update a stick that is on the watch connection, bump `FW_VERSION`, build, and stage the application image (not
the merged one):

```sh
talkstick ota ~/.cache/talkstick/build/talkstick.ino.bin
```

The daemon offers it (`OTA <size> <sha256> <mac> <version>`); the stick checks the HMAC, waits until it is idle,
fetches the image with `F <version>`, writes it to the other app slot while hashing it, and switches only if the
SHA-256 matches. The screen shows Updating, then Restarting.

Rollback: the new image boots on probation. It is marked valid when its watch connection gets the first `TIME`.
If that has not happened within 120 s, it logs why and rolls back to the previous slot; a crash before then rolls
back too. Idle shows `Updated to 0.3.x` for 2 minutes after a good update. `status` shows the running partition
and whether it is on probation.

## Build and flash

Needs `arduino-cli` with the M5Stack board index (`m5stack:esp32` core 3.3.10, board
`m5stack:esp32:m5stack_sticks3:PartitionScheme=default_8MB`, two 3.2 MB app slots), the M5Unified (>= 0.2.12,
built with 0.3.0), M5GFX (0.3.0) and M5PM1 (1.0.7) libraries, and `esptool`.

```sh
mkdir -p ~/.config/talkstick
cp firmware/config.example.env ~/.config/talkstick/firmware.env   # fill in WiFi, service, port, fallback host
chmod 600 ~/.config/talkstick/firmware.env
firmware/build.sh                        # or: firmware/build.sh a.env b.env (later files win)
firmware/flash.sh                        # PORT=/dev/cu.usbmodemXXXX to pick the port
```

`build.sh` turns the config into a header in `TALKSTICK_GEN_DIR` (default `~/.config/talkstick/gen`) and builds into
`TALKSTICK_BUILD_DIR` (default `~/.cache/talkstick/build`). Both directories are created mode 700 because the header
and the image contain the WiFi password and the update key. `TALKSTICK_*` environment variables override the config
files. It compiles under `nice -n 10` with `--jobs 2`; `TALKSTICK_WARNINGS=all` shows every warning.

`flash.sh` writes the merged image over USB with `--before default-reset --after watchdog-reset` (a hard reset left
the chip in download mode over USB-Serial/JTAG). If it cannot reach the bootloader, hold the power button until the
green LED blinks (ROM download mode) and run it again.

## Serial console and bench tools

115200 baud on the USB port. The stick logs boot, WiFi, discovery, screens, diagnostics and byte counts.

| Command | Effect |
|---|---|
| `ptt 3` | act as if button A were held for 3 s (the mic records for real) |
| `press a`, `press b` | tap button A (status request) or press button B |
| `pcm <bytes>` | answers `pcm ready`, then takes that many bytes of 16 kHz mono int16 PCM and sends them as an utterance |
| `shot` | dumps the screen as `SHOT <w> <h> RGB565BE <bytes>`, the pixels, `SHOT END` |
| `state` | prints the current screen and phase |
| `status` | version, partition, probation, phase, link, WiFi and power save, target, waiting/busy/ahead/paused, inbox count, battery, VBUS, G11 level, clock, free heap and PSRAM |
| `link`, `ping` | link state, target, seconds since the last watch line, diagnostics ring length |
| `inbox` | stored answers: id, outcome, read, first 40 characters |
| `demo <name>` | shows a screen for 10 s with made-up state, for stills: idle, idle-noconn, listening, sending, delivered, thinking, queued, paused, reply, reply-long, noreply, notsent-retry, notsent, notheard, offline, cancelled, updating, updated, lowbatt |
| `wifi drop <s>` | drops WiFi for that many seconds, then rejoins |
| `scan` | reports whether the configured WiFi name is visible (exact or case-only match), channel, signal, security; never prints names |
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
