# Talk to a T3 Code agent thread

Press the stick, say something, and it lands as a message in a [T3 Code](https://t3.codes) thread. The agent's reply comes back to your phone through any notify script you have.

`deliver.py` follows the talkstick delivery contract.

1. It reads the transcript on stdin.
2. It checks the thread is live, meaning not settled and not archived. If it is not, it exits 3 and the stick shows OFFLINE.
3. It sends the transcript with `t3 thread send <id> -` and prints `@sent`, so the stick shows SENT. It then prints `@busy` while the thread runs some other turn and `@running` once its own message's turn runs, so the stick can show its place in the queue.
4. It polls `t3 thread show --json` every 3 s, for up to 10 minutes. It waits for the run that its own message started, then prints that run's last assistant message. A busy thread queues the message as its next turn. Matching on the run, not on "any new assistant text", keeps an unrelated reply from being returned.

Nothing else from the thread transcript is printed or logged.

## Setup

| Variable | Meaning |
|---|---|
| `T3_BIN` | path to the `t3` CLI |
| `T3_THREAD` | thread id, or |
| `T3_THREAD_FILE` | a file whose first non-comment token is the thread id, for a target thread that rolls over |
| `T3_PREFIX` | text put before the transcript, default `[stick] ` |
| `T3_REPLY_TIMEOUT`, `T3_POLL` | seconds, default 600 and 3 |

1. Copy `config.json` to `~/.config/talkstick/config.json` and fix the paths.
2. Fill in `talkstick.plist.template` and load it with `launchctl bootstrap`.
3. Try it with the config's `"dry": true` first. In dry mode `deliver.py` only checks that the thread is live and sends nothing.

```sh
TALKSTICK_DRY=1 T3_BIN=... T3_THREAD=... python3 deliver.py <<< "hello"   # prints "(dry) thread live, ..."
talkstick fake-stick hello.wav                                          # with the daemon running
```
