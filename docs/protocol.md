# talkstick wire protocol (M5R1)

Small enough to implement on any WiFi device with a mic.

## Discovery

The daemon advertises a Bonjour service (default `_m5remote._tcp`, port 47820). The stick tries, in order: the
IP:port that worked last time (kept across deep sleep), the Bonjour service, then a fallback host name set at build
time.

## Connection

One TCP connection per utterance (or per ping), opened by the stick.

### Stick to daemon

First a line of ASCII:

```
M5R1 <device-id> <battery-percent> <firmware-version>\n
```

Then frames: 1 byte type, 2 bytes little-endian payload length, payload.

| Type | Payload | Meaning |
|---|---|---|
| `A` | up to 4096 bytes | audio: signed 16-bit little-endian PCM, 16000 Hz, mono |
| `E` | none | end of utterance (button released) |
| `S` | none | status request (button tapped): the daemon drops any audio and delivers its configured `status_prompt` |
| `C` | none | cancel: the daemon drops the audio |
| `P` | none | ping: the daemon answers `TIME` (and `LAST`), then the stick closes |

Audio captured before the socket is up is buffered on the stick and sent first.

### Daemon to stick

ASCII lines on the same socket.

| Line | When |
|---|---|
| `TIME <unix-seconds> <utc-offset-seconds>` | right after every hello line |
| `LAST <text>` | on a ping, the last reply, if there is one |
| `RECV` | end of utterance received |
| `WAIT <seconds>` | the engine is waiting before it can run (optional) |
| `SENT [first 40 characters]` | the text reached the delivery target |
| `REPLY <text>` | the reply, at most 600 characters, newlines written as the two characters `\n`, UTF-8 |
| `REPLIED` | done; the stick shows the reply |
| `OFFLINE` | the delivery target is offline |
| `ERR <short reason>` | anything else went wrong (empty transcript, engine or delivery failure) |

The stick keeps the socket open until `REPLIED`, `OFFLINE` or `ERR`, or 11 minutes, whichever comes first.
`on_reply` (if configured) runs after `REPLIED`, so the stick never waits on it.

## Queue, watch and over-the-air updates (firmware 0.3 and later)

Everything above still holds, so an older stick keeps working: the utterance socket still gets `REPLY` and
`REPLIED`. A newer stick adds one long-lived *watch* connection and closes each utterance socket as soon as the
words are delivered, so it can be pressed again while earlier replies are still being written.

### More frames, stick to daemon

| Type | Payload | Meaning |
|---|---|---|
| `W` | none | watch: keep this connection open and push queue state, answers and update offers on it |
| `K` | answer id (ASCII) | ack: the stick has stored that answer; the daemon stops offering it |
| `F` | version (ASCII) | fetch the staged firmware image with that version |

### More lines on an utterance or status connection

| Line | When |
|---|---|
| `ID <id>` | right after `RECV`; `<id>` is 8 lowercase hex characters naming this utterance |

`SENT` means the words are in the delivery target. A watching stick closes the socket after `SENT`, `OFFLINE` or
`ERR`. The daemon goes on waiting for the reply, and writes it to this socket only if the stick kept it open.

The daemon delivers utterances in the order their `E` (or `S`) arrived and never waits for one reply before
delivering the next.

### Lines on a watch connection

| Line | When |
|---|---|
| `TIME <unix-seconds> <utc-offset-seconds>` | on connect, then every 5 s; a stick that hears nothing for 12 s treats the Mac as gone and reconnects |
| `QUEUE <waiting> <busy> <ahead>` | on connect and whenever it changes. `waiting`: this device's delivered utterances with no answer yet. `busy`: 1 when the target is working on something that is not one of them. `ahead`: turns before the newest waiting utterance starts (0 when it is running or unknown) |
| `ANSWER <id> <outcome> <said>\t<text>` | on connect for every answer not yet acked (oldest first), then as each arrives. `outcome` is `replied`, `offline` or `error`. `said` is the first 40 characters of the transcript (no tab or newline). `text` is the reply in the form of `REPLY` (at most `stick_reply_chars`, newlines as `\n`, tabs as spaces), or the reason for `offline` and `error` |
| `OTA <size> <sha256-hex> <mac-hex> <version>` | on connect, and when an image is staged, if the stick's hello named a different version |

The daemon keeps up to 8 unacked answers per device across restarts.

### Over-the-air updates

`talkstick ota <image.bin>` stages an application image (the `.ino.bin`, not the merged image) and waits until the
stick says hello with the new version. The offer's `mac` is HMAC-SHA256 over the ASCII string
`talkstick-ota <sha256-hex> <version>`, keyed with the 32-byte key from `ota_key_file`, which the firmware build
also embeds. The stick ignores an offer whose mac does not match. Otherwise it opens a new connection, sends the
hello and `F <version>`, and the daemon answers:

```
IMAGE <size> <sha256-hex>\n<size raw bytes>
```

then closes, or `ERR no image`. The stick writes the bytes to the idle app slot, checks the SHA-256, and only then
switches slots and restarts. The new image stays on probation until its watch connection gets `TIME`. If that has
not happened within 120 s, or the image crashes first, the bootloader returns to the previous slot.
