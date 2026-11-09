#!/usr/bin/env python3
"""talkstick delivery command: send the transcript to a T3 Code thread and print the agent's reply.

Reads the transcript on stdin. Follows the talkstick delivery contract: prints "@sent" once `t3 thread send`
succeeds, then the reply text; exit 3 when the thread is missing, settled or archived.

Required environment:
  T3_BIN          path to the t3 CLI
  T3_THREAD_FILE  file whose first non-comment token is the thread id (lets the target thread roll over),
  or T3_THREAD    the thread id itself
Optional: T3_PREFIX (default "[stick] "), T3_REPLY_TIMEOUT seconds (default 600), T3_POLL seconds (default 3).

TALKSTICK_DRY=1 checks the thread is live and sends nothing.
Thread transcripts can hold private text, so nothing from the thread is printed except the reply itself.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

EXIT_OFFLINE = 3


def die(msg: str, code: int = 1) -> None:
    print(f"deliver.py: {msg}", file=sys.stderr)
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


def run_ordinal(run_id: str | None) -> int:
    m = re.search(r":ordinal:(\d+)$", run_id or "")
    return int(m.group(1)) if m else -1


def find_reply(state: dict, sent: str, base: int, run_id: str | None) -> tuple[str | None, str | None]:
    """(run_id, reply). The run is the one our user message started, matched by text and a newer ordinal, so a
    busy thread that queues the message is never answered with some other run's text. reply is None until that
    run has finished."""
    msgs = state.get("messages") or []
    if run_id is None:
        for m in msgs:
            if m.get("role") == "user" and run_ordinal(m.get("runId")) > base \
                    and (m.get("text") or "").strip().endswith(sent):
                run_id = m["runId"]
    if run_id is None or (state.get("thread") or {}).get("activeRunId") == run_id:
        return run_id, None
    replies = [m.get("text") or "" for m in msgs if m.get("role") == "assistant" and m.get("runId") == run_id]
    return run_id, (replies[-1] if replies else "")


def main() -> None:
    t3 = os.environ.get("T3_BIN") or die("set T3_BIN", 2)
    text = sys.stdin.read().strip()
    if not text:
        die("empty transcript", 2)
    tid = thread_id()
    state = show(t3, tid, 5) if tid else None
    if not is_live(state):
        sys.exit(EXIT_OFFLINE)
    msg = os.environ.get("T3_PREFIX", "[stick] ") + text
    if os.environ.get("TALKSTICK_DRY") == "1":
        print(f"(dry) thread live, would send {len(msg)} chars")
        return
    base = run_ordinal(state["thread"].get("latestRunId"))
    p = subprocess.run([t3, "thread", "send", tid, "-"], input=msg, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        die(f"t3 thread send exit {p.returncode}")
    print("@sent", flush=True)
    deadline = time.time() + float(os.environ.get("T3_REPLY_TIMEOUT", "600"))
    poll = float(os.environ.get("T3_POLL", "3"))
    run_id, empty_polls = None, 0
    while time.time() < deadline:
        time.sleep(poll)
        state = show(t3, tid, 40)
        if not state:
            continue
        run_id, reply = find_reply(state, text, base, run_id)
        if reply is None:
            continue
        if reply.strip():
            print(reply.strip())
            return
        empty_polls += 1
        if empty_polls >= 3:
            print("(the agent finished with no text reply)")
            return
    die("no reply before timeout")


if __name__ == "__main__":
    main()
