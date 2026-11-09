#!/usr/bin/env python3
"""talkstick delivery command: send the transcript to a T3 Code thread and print the agent's reply.

Reads the transcript on stdin and sends it as "[stick HH:MM:SS] text", stamped with the time it was spoken
(TALKSTICK_SPOKEN_AT, epoch seconds, set by the daemon), or "[stick HH:MM:SS, N min late] text" when the hand-off
is a minute or more after that. Follows the talkstick delivery contract: prints "@sent" once `t3 thread send`
returns our run, then status lines, each only when it changes: "@busy" (our run is queued behind another),
"@paused" (our run is queued and T3's queue is paused after a stopped turn), "@running" (our run has started);
then the reply text, the run's last assistant message. Exit 3 when the thread is missing, settled or archived;
exit 4 with "still queued in T3" on stderr when the run never started.

Required environment:
  T3_BIN          path to the t3 CLI
  T3_THREAD_FILE  file whose first non-comment token is the thread id (lets the target thread roll over),
  or T3_THREAD    the thread id itself
Optional: T3_PREFIX (the word before the time, default "stick"), T3_REPLY_TIMEOUT seconds from the run's start
(default 600), T3_QUEUE_TIMEOUT seconds a run may stay queued (default 3600), T3_POLL seconds (default 3).

TALKSTICK_DRY=1 checks the thread is live and sends nothing.
Thread transcripts can hold private text, so nothing from the thread is printed except the reply itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

EXIT_OFFLINE = 3
EXIT_QUEUED = 4
SEND_TIMEOUT = 20
SEND_ATTEMPTS = 2
PAUSED_POLLS = 2


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def thread_id() -> str | None:
    if os.environ.get("T3_THREAD"):
        return os.environ["T3_THREAD"].strip()
    path = os.environ.get("T3_THREAD_FILE")
    if not path:
        die("set T3_THREAD or T3_THREAD_FILE", 2)
    try:
        for line in open(os.path.expanduser(path)):
            line = line.strip()
            if line and not line.startswith("#"):
                return line.split()[0]
    except OSError:
        return None
    return None


def show(t3: str, tid: str, messages: int) -> dict | None:
    p = subprocess.run([t3, "thread", "show", tid, "--messages", str(messages), "--json"],
                       capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


def is_live(state: dict | None) -> bool:
    t = (state or {}).get("thread") or {}
    return bool(t) and t.get("settled") is False and t.get("archived") is False


def stamp(spoken_at: float, handed_at: float) -> str:
    who = os.environ.get("T3_PREFIX", "stick")
    clock = time.strftime("%H:%M:%S", time.localtime(spoken_at))
    late = handed_at - spoken_at
    return f"[{who} {clock}, {int(late // 60)} min late] " if late >= 60 else f"[{who} {clock}] "


def send(t3: str, tid: str, msg: str) -> str:
    """Our run id from `t3 thread send --json`."""
    for _ in range(SEND_ATTEMPTS):
        try:
            p = subprocess.run([t3, "thread", "send", "--json", tid, "-"], input=msg, capture_output=True,
                               text=True, timeout=SEND_TIMEOUT)
        except subprocess.TimeoutExpired:
            continue
        try:
            run_id = json.loads(p.stdout).get("runId") if p.returncode == 0 else None
        except (ValueError, AttributeError):
            run_id = None
        if run_id:
            return run_id
    die("t3 thread send failed")


def stage(state: dict, run_id: str) -> tuple[str, str | None]:
    """("queued" | "running" | "done", reply). A queued run's messages are not shown until it starts; reply is the
    run's last assistant text once it is done."""
    msgs = [m for m in state.get("messages") or [] if m.get("runId") == run_id]
    if (state.get("thread") or {}).get("activeRunId") == run_id:
        return "running", None
    if not msgs:
        return "queued", None
    replies = [m.get("text") or "" for m in msgs if m.get("role") == "assistant"]
    return "done", (replies[-1] if replies else "")


def main() -> None:
    t3 = os.environ.get("T3_BIN") or die("set T3_BIN", 2)
    text = sys.stdin.read().strip()
    if not text:
        die("empty transcript", 2)
    tid = thread_id()
    state = show(t3, tid, 5) if tid else None
    if not is_live(state):
        sys.exit(EXIT_OFFLINE)
    now = time.time()
    msg = stamp(float(os.environ.get("TALKSTICK_SPOKEN_AT") or now), now) + text
    if os.environ.get("TALKSTICK_DRY") == "1":
        print(f"(dry) thread live, would send {len(msg)} chars")
        return
    run_id = send(t3, tid, msg)
    print("@sent", flush=True)
    queue_deadline = time.time() + float(os.environ.get("T3_QUEUE_TIMEOUT", "3600"))
    reply_timeout = float(os.environ.get("T3_REPLY_TIMEOUT", "600"))
    poll = float(os.environ.get("T3_POLL", "3"))
    shown, started, paused_polls, empty_polls = None, None, 0, 0

    def mark(line: str) -> None:
        nonlocal shown
        if line != shown:
            print(line, flush=True)
            shown = line

    while True:
        state = show(t3, tid, 40)
        if state:
            where, reply = stage(state, run_id)
            t = state.get("thread") or {}
            if where == "queued":
                stuck = t.get("status") == "queued" and not t.get("activeRunId")
                paused_polls = paused_polls + 1 if stuck else 0
                if paused_polls >= PAUSED_POLLS:
                    mark("@paused")
                elif t.get("activeRunId"):
                    mark("@busy")
            else:
                started = started or time.time()
                mark("@running")
            if reply and reply.strip():
                print(reply.strip())
                return
            if reply is not None:
                empty_polls += 1
                if empty_polls >= 3:
                    print("(the agent finished with no text reply)")
                    return
        if started is None and time.time() > queue_deadline:
            die("still queued in T3", EXIT_QUEUED)
        if started is not None and time.time() > started + reply_timeout:
            die("no reply before timeout")
        time.sleep(poll)


if __name__ == "__main__":
    main()
