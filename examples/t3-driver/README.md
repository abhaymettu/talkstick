# Talk to a T3 Code agent thread

Press the stick, say something, and it lands as a message in a [T3 Code](https://t3.codes) thread. The agent's reply comes back to your phone through any notify script you have.

`deliver.py` follows the talkstick delivery contract.

1. It reads the transcript on stdin.
2. It checks the thread is live, meaning not settled and not archived. If it is not, it exits 3 and the stick shows OFFLINE.
3. It sends `[stick HH:MM:SS] <transcript>` with `t3 thread send --json` (20 s timeout, two attempts), stamped with the time you spoke (`TALKSTICK_SPOKEN_AT`, set by the daemon). A hand-off a minute or more later reads `[stick HH:MM:SS, N min late]`. The send returns the id of the run this message starts, and it prints `@sent`, so the stick shows SENT.
4. It polls `t3 thread show --json` every 3 s and prints, each only when it changes: `@busy` while its run is queued behind another, `@paused` when its run is queued and T3's queue is paused (after a stopped turn, T3 holds the queue until something new is sent), and `@running` once its run starts. When the run ends it prints that run's last assistant message. A run that ends with no text, such as a stopped turn, prints "(the agent finished with no text reply)". Matching on the run id, not on "any new assistant text", keeps an unrelated reply from being returned.
5. It waits up to 10 minutes from the run's start for the reply, and up to an hour for the run to start. A run still queued after that exits 4 with "still queued in T3", which the stick shows as an error.

Nothing else from the thread transcript is printed or logged.

## Setup

| Variable | Meaning |
|---|---|
| `T3_BIN` | path to the `t3` CLI |
| `T3_THREAD` | thread id, or |
| `T3_THREAD_FILE` | a file whose first non-comment token is the thread id, for a target thread that rolls over |
| `T3_PREFIX` | the word before the spoken time, default `stick` |
| `T3_REPLY_TIMEOUT`, `T3_QUEUE_TIMEOUT`, `T3_POLL` | seconds, default 600 (from the run's start), 3600 (while queued) and 3 |

1. Copy `config.json` to `~/.config/talkstick/config.json` and fix the paths.
2. Fill in `talkstick.plist.template` and load it with `launchctl bootstrap`.
3. Try it with the config's `"dry": true` first. In dry mode `deliver.py` only checks that the thread is live and sends nothing.

```sh
TALKSTICK_DRY=1 T3_BIN=... T3_THREAD=... python3 deliver.py <<< "hello"   # prints "(dry) thread live, ..."
talkstick fake-stick hello.wav                                          # with the daemon running
```
