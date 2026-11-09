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
