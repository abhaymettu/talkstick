"""Run: python3 -m unittest discover -s tests"""
import contextlib
import hashlib
import hmac
import importlib.machinery
import importlib.util
import io
import json
import os
import socket
import subprocess
import time
import struct
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("talkstick", str(ROOT / "daemon/talkstick"))
_spec = importlib.util.spec_from_loader("talkstick", _loader)
ts = importlib.util.module_from_spec(_spec)
_loader.exec_module(ts)


def frame(kind: bytes, payload: bytes = b"") -> bytes:
    return kind + struct.pack("<H", len(payload)) + payload


def stream(*frames: bytes, header: bytes = b"M5R1 aabbcc 87 1.0\n") -> io.BytesIO:
    return io.BytesIO(header + b"".join(frames))


def py(code: str) -> list:
    return [sys.executable, "-c", code]


class FrameParser(unittest.TestCase):
    def test_audio_then_end(self):
        u = ts.read_utterance(stream(frame(b"A", b"\x01\x00\x02\x00"), frame(b"A", b"\x03\x00"), frame(b"E")))
        self.assertEqual(u.outcome, "end")
        self.assertEqual(u.pcm, b"\x01\x00\x02\x00\x03\x00")
        self.assertEqual((u.header["device"], u.header["battery"], u.header["fw"]), ("aabbcc", "87", "1.0"))

    def test_cancel_drops(self):
        u = ts.read_utterance(stream(frame(b"A", b"\x00\x00"), frame(b"C"), frame(b"A", b"\x00\x00")))
        self.assertEqual(u.outcome, "cancel")

    def test_bad_header(self):
        self.assertEqual(ts.read_utterance(stream(frame(b"E"), header=b"HELLO\n")).outcome, "bad_header")

    def test_truncated_frame(self):
        self.assertEqual(ts.read_utterance(stream(b"A\x10\x00\x00\x00")).outcome, "truncated")

    def test_status_request(self):
        u = ts.read_utterance(stream(frame(b"A", b"\x00\x00"), frame(b"S")))
        self.assertEqual((u.outcome, u.pcm), ("status", b""))

    def test_ping(self):
        self.assertEqual(ts.read_utterance(stream(frame(b"P"))).outcome, "ping")

    def test_on_header_runs_before_frames(self):
        seen = []
        ts.read_utterance(stream(frame(b"P")), on_header=lambda h: seen.append(h["device"]))
        self.assertEqual(seen, ["aabbcc"])

    def test_eof_before_end(self):
        self.assertEqual(ts.read_utterance(stream(frame(b"A", b"\x00\x00"))).outcome, "truncated")


class CommandDelivery(unittest.TestCase):
    def deliver(self, code: str):
        sent, rec = [], {"device": "d1", "utterance_id": "u1"}
        reply = ts.deliver_command({"argv": py(code), "timeout": 20}, "hello there", rec, False,
                                   lambda: sent.append(1))
        return reply, sent, rec

    def test_sent_then_reply(self):
        reply, sent, _ = self.deliver(
            "import sys; t=sys.stdin.read(); print('@sent', flush=True); print('got: ' + t)")
        self.assertEqual(reply, "got: hello there")
        self.assertGreaterEqual(len(sent), 1)

    def test_no_sent_line_still_sends_at_exit(self):
        reply, sent, _ = self.deliver("print('ok')")
        self.assertEqual(reply, "ok")
        self.assertEqual(len(sent), 1)

    def test_at_sent_inside_reply_is_text(self):
        reply, _, _ = self.deliver("print('first'); print('@sent')")
        self.assertEqual(reply, "first\n@sent")

    def test_exit_3_is_offline(self):
        with self.assertRaises(ts.Offline):
            self.deliver("import sys; sys.exit(3)")

    def test_other_exit_is_error(self):
        with self.assertRaises(ts.StepError) as cm:
            self.deliver("import sys; sys.exit(5)")
        self.assertEqual(str(cm.exception), "delivery exit 5")

    def test_env_carries_dry_and_ids(self):
        rec = {"device": "d1", "utterance_id": "u1"}
        reply = ts.deliver_command(
            {"argv": py("import os; print(os.environ['TALKSTICK_DRY'], os.environ['TALKSTICK_DEVICE'],"
                        " os.environ['TALKSTICK_UTTERANCE_ID'])")}, "x", rec, True, lambda: None)
        self.assertEqual(reply, "1 d1 u1")


class Encoding(unittest.TestCase):
    def test_time_line(self):
        self.assertRegex(ts.time_line(1794200000), r"^TIME 1794200000 -?\d+$")

    def test_stick_line_newlines_and_cap(self):
        self.assertEqual(ts.stick_line("a\r\nb\nc"), "a\\nb\\nc")
        self.assertEqual(ts.stick_line("abc\nd", 4), "abc")
        self.assertEqual(ts.stick_line("abc\nd", 5), "abc\\n")
        self.assertEqual(len(ts.stick_line("\n" * 400)), 0)
        self.assertEqual(len(ts.stick_line("é" * 700)), 600)


class Webhook(unittest.TestCase):
    def test_parse_json_reply(self):
        self.assertEqual(ts.parse_webhook_reply("application/json", b'{"reply": "hi"}'), "hi")

    def test_parse_plain_text(self):
        self.assertEqual(ts.parse_webhook_reply("text/plain", b"  plain reply \n"), "plain reply")

    def test_parse_json_without_reply(self):
        self.assertEqual(ts.parse_webhook_reply("application/json", b'{"ok": true}'), "")

    def test_live_server_reply_and_503(self):
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if body["text"] == "down":
                    self.send_response(503)
                    self.end_headers()
                    return
                out = json.dumps({"reply": "echo " + body["text"] + " " + body["utterance_id"]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cfg = {"url": f"http://127.0.0.1:{srv.server_port}/", "timeout": 5}
        try:
            rec = {"device": "d", "utterance_id": "u9"}
            self.assertEqual(ts.deliver_webhook(cfg, "hi", rec, True), "echo hi u9")
            with self.assertRaises(ts.Offline):
                ts.deliver_webhook(cfg, "down", {}, True)
        finally:
            srv.shutdown()
            srv.server_close()


class Pipeline(unittest.TestCase):
    def daemon(self, delivery_code: str, **over):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        tmp = holder.name
        cfg = ts.merge(ts.DEFAULTS, {
            "state_dir": tmp, "advertise": False,
            "engine": {"type": "command", "command": {"argv": py("print('turn on the lights')")}},
            "delivery": {"type": "command", "command": {"argv": py(delivery_code)}}, **over})
        return ts.Daemon(cfg), tmp

    def run_stick(self, d):
        lines = []
        d.handle_stick(stream(frame(b"A", b"\x00\x00" * 100), frame(b"E")), lines.append, "127.0.0.1")
        return lines, [json.loads(l) for l in d.events.read_text().splitlines()]

    def test_round_trip_lines_and_timings(self):
        d, _ = self.daemon("import sys; sys.stdin.read(); print('@sent', flush=True); print('done')")
        lines, events = self.run_stick(d)
        row = events[-1]
        self.assertRegex(lines[0], r"^TIME \d+ -?\d+$")
        self.assertEqual(lines[1:], ["RECV", "ID " + row["utterance_id"][:8], "SENT turn on the lights", "REPLY done",
                                     "REPLIED"])
        self.assertEqual(row["outcome"], "replied")
        for k in ("d_transcript", "d_sent", "d_reply", "d_done"):
            self.assertIn(k, row)

    def test_failing_on_reply_keeps_the_reply(self):
        d, _ = self.daemon("print('done')", on_reply=py("import sys; sys.exit(1)"))
        lines, _ = self.run_stick(d)
        d.wait_replies()
        events = [json.loads(l) for l in d.events.read_text().splitlines()]
        self.assertEqual(lines[-2:], ["REPLY done", "REPLIED"])
        self.assertEqual([e for e in events if e.get("event") == "utterance"][-1]["outcome"], "replied")
        self.assertEqual([e for e in events if e.get("event") == "on_reply"][-1]["rc"], 1)

    def test_on_reply_timeout_is_logged_not_raised(self):
        d, _ = self.daemon("print('done')", on_reply=py("import time; time.sleep(5)"), on_reply_timeout=0.2)
        lines, _ = self.run_stick(d)
        d.wait_replies()
        events = [json.loads(l) for l in d.events.read_text().splitlines()]
        self.assertEqual(lines[-1], "REPLIED")
        row = [e for e in events if e.get("event") == "on_reply"][-1]
        self.assertEqual(row["rc"], "failed")
        self.assertIn("TimeoutExpired", row["error"])

    def test_slow_on_reply_does_not_hold_the_next_utterance(self):
        d, _ = self.daemon("print('done')", on_reply=py("import time; time.sleep(3)"))
        self.run_stick(d)
        t0 = time.time()
        lines, _ = self.run_stick(d)
        self.assertEqual(lines[-1], "REPLIED")
        self.assertLess(time.time() - t0, 2.5)
        d.wait_replies()

    def test_offline(self):
        d, _ = self.daemon("import sys; sys.exit(3)")
        lines, events = self.run_stick(d)
        self.assertEqual(lines[1:], ["RECV", "ID " + events[-1]["utterance_id"][:8], "OFFLINE"])
        self.assertEqual(events[-1]["outcome"], "offline")

    def test_cancel_logs_no_utterance(self):
        d, _ = self.daemon("print('x')")
        lines = []
        d.handle_stick(stream(frame(b"A", b"\x00\x00"), frame(b"C")), lines.append, "127.0.0.1")
        self.assertEqual([l.split()[0] for l in lines], ["TIME"])
        self.assertEqual(json.loads(d.events.read_text().splitlines()[-1])["event"], "cancel")

    def stick(self, d, *frames):
        lines = []
        d.handle_stick(stream(*frames), lines.append, "127.0.0.1")
        return lines

    def test_status_request_runs_prompt(self):
        d, _ = self.daemon("import sys; t=sys.stdin.read(); print('@sent', flush=True); print('status of: ' + t)",
                           status_prompt="what is running")
        lines = self.stick(d, frame(b"A", b"\x00\x00"), frame(b"S"))
        row = json.loads(d.events.read_text().splitlines()[-1])
        self.assertEqual(lines[1:], ["RECV", "ID " + row["utterance_id"][:8], "SENT what is running",
                                     "REPLY status of: what is running", "REPLIED"])
        self.assertEqual((row["kind"], row["outcome"]), ("status", "replied"))

    def test_status_request_without_prompt(self):
        d, _ = self.daemon("print('x')")
        self.assertEqual(self.stick(d, frame(b"S"))[1:], ["ERR no status_prompt"])

    def test_reply_multiline_encoded_and_persisted_for_ping(self):
        d, _ = self.daemon("print('line one'); print('line two')")
        self.assertEqual(self.stick(d, frame(b"A", b"\x00\x00"), frame(b"E"))[-2],
                         "REPLY line one\\nline two")
        ping = self.stick(d, frame(b"P"))
        self.assertRegex(ping[0], r"^TIME ")
        self.assertEqual(ping[1:], ["LAST line one\\nline two"])

    def test_ping_without_history_is_time_only(self):
        d, _ = self.daemon("print('x')")
        self.assertEqual([l.split()[0] for l in self.stick(d, frame(b"P"))], ["TIME"])

    def test_reply_capped_at_stick_reply_chars(self):
        d, _ = self.daemon("print('x' * 900)")
        reply = self.stick(d, frame(b"A", b"\x00\x00"), frame(b"E"))[-2]
        self.assertEqual(reply, "REPLY " + "x" * 600)

    def test_trim(self):
        self.assertEqual(ts.trim_reply("x" * 10, 4, " (more)"), "xxxx (more)")
        self.assertEqual(ts.trim_reply("short", 600, " (more)"), "short")
        self.assertEqual(ts.trim_reply("x" * 10, None, " (more)"), "x" * 10)

ENGINE = ("import sys, time, wave; n = wave.open(sys.argv[1]).getnframes();"
          " time.sleep(float(sys.argv[2]) if n == 100 else 0); print('utt%d' % n)")

DELIVER = """
import os, sys, time
d, t = sys.argv[1], sys.stdin.read().strip()
open(os.path.join(d, 'delivered'), 'a').write(t + '\\n')
print('@sent', flush=True)
for step in sys.argv[2:]:
    if step.startswith('@'):
        print(step, flush=True)
    else:
        while not os.path.exists(os.path.join(d, step + '-' + t)):
            time.sleep(0.02)
print('re ' + t)
"""

KEY = bytes(range(32))
IMAGE = b"\xe9" + b"\x00" * 100 + b"talkstick-0.3.1\x00" + b"\xff" * 50


class Queue(unittest.TestCase):
    """Utterances from one stick: delivered in press order without waiting for replies, answers on a watch."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)

    def daemon(self, steps=(), slow=0.0, **over):
        self.cfg = ts.merge(ts.DEFAULTS, {
            "state_dir": str(self.tmp / "state"), "advertise": False, "ota_key_file": str(self.tmp / "ota.key"),
            "engine": {"type": "command", "command": {"argv": py(ENGINE) + ["{wav}", str(slow)]}},
            "delivery": {"type": "command", "command": {"argv": py(DELIVER) + [str(self.tmp), *steps]}}, **over})
        return ts.Daemon(self.cfg)

    def press(self, d, n, sid=b""):
        lines = []
        t = threading.Thread(target=d.handle_stick, daemon=True, args=(
            stream(frame(b"A", b"\x00\x00" * n), frame(b"E", sid)), lines.append, "127.0.0.1"))
        t.start()
        return t, lines

    def until(self, cond, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return
            time.sleep(0.02)
        self.fail("condition not met in time")

    def touch(self, name):
        (self.tmp / name).touch()

    def delivered(self):
        return (self.tmp / "delivered").read_text().split()

    def events(self, d, kind):
        return [r for r in map(json.loads, d.events.read_text().splitlines()) if r.get("event") == kind]

    def serve(self, d):
        srv = d.make_server(("127.0.0.1", 0))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def watch(self, port, fw="talkstick-0.3.0"):
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.addCleanup(s.close)
        s.sendall(f"M5R1 aabbcc 87 {fw}\n".encode() + frame(b"W"))
        f = s.makefile("rb")
        self.assertRegex(f.readline().decode(), r"^TIME \d+ -?\d+\n$")
        return s, f

    def next_line(self, f):
        while True:
            line = f.readline().decode().rstrip("\n")
            if not line.startswith("TIME "):
                return line

    def quick_watch(self, d, fw="1.0", *frames):
        lines = []
        d.handle_stick(stream(frame(b"W"), *frames, header=f"M5R1 aabbcc 87 {fw}\n".encode()), lines.append, "x")
        return lines[1:]

    def test_second_utterance_delivered_while_first_waits_for_its_reply(self):
        d = self.daemon(["go"])
        t1, l1 = self.press(d, 100)
        self.until(lambda: "SENT utt100" in l1)
        t2, l2 = self.press(d, 200)
        self.until(lambda: "SENT utt200" in l2)
        self.assertNotIn("REPLIED", l1)
        self.touch("go-utt200")
        t2.join(10)
        self.assertEqual(l2[-2:], ["REPLY re utt200", "REPLIED"])
        self.touch("go-utt100")
        t1.join(10)
        self.assertEqual(l1[-2:], ["REPLY re utt100", "REPLIED"])
        self.assertEqual(self.delivered(), ["utt100", "utt200"])

    def test_press_order_kept_when_the_first_transcribes_slowest(self):
        d = self.daemon(slow=0.8)
        threads = []
        for n in (100, 200, 300):
            t, lines = self.press(d, n)
            self.until(lambda: "RECV" in lines)
            threads.append(t)
        for t in threads:
            t.join(10)
        self.assertEqual(self.delivered(), ["utt100", "utt200", "utt300"])

    def test_received_and_delivered_events_before_the_final_row(self):
        d = self.daemon()
        t, lines = self.press(d, 100)
        t.join(10)
        rows = [json.loads(l) for l in d.events.read_text().splitlines()]
        self.assertEqual([r["event"] for r in rows], ["received", "delivered", "utterance"])
        uid = rows[2]["utterance_id"]
        self.assertEqual(lines[2], "ID " + uid[:8])
        self.assertRegex(lines[2], r"^ID [0-9a-f]{8}$")
        self.assertEqual({k: rows[0][k] for k in ("utterance_id", "id8", "device", "kind", "audio_s")},
                         {"utterance_id": uid, "id8": uid[:8], "device": "aabbcc", "kind": "utterance",
                          "audio_s": 0.01})
        self.assertEqual(rows[1]["id8"], uid[:8])
        self.assertEqual(rows[1]["d_sent"], rows[2]["d_sent"])

    def test_watch_gets_queue_and_answer_and_ack_removes_it(self):
        d = self.daemon(["go"])
        s, f = self.watch(self.serve(d))
        self.assertEqual(self.next_line(f), "QUEUE 0 0 0 0")
        t, lines = self.press(d, 100)
        self.assertEqual(self.next_line(f), "QUEUE 1 0 0 0")
        self.touch("go-utt100")
        t.join(10)
        aid = lines[2].split()[1]
        self.assertEqual(self.next_line(f), f"ANSWER {aid} replied utt100\tre utt100")
        self.assertEqual(self.next_line(f), "QUEUE 0 0 0 0")
        self.assertEqual(self.quick_watch(d)[1:], [f"ANSWER {aid} replied utt100\tre utt100"])
        s.sendall(frame(b"K", aid.encode()))
        self.until(lambda: json.loads((self.tmp / "state/answers.json").read_text())["answers"] == {"aabbcc": []})
        self.assertEqual(self.quick_watch(d), ["QUEUE 0 0 0 0"])

    def test_unacked_answers_survive_a_restart_and_keep_the_newest_eight(self):
        d = self.daemon()
        for n in range(100, 110):
            self.press(d, n)[0].join(10)
        lines = self.quick_watch(ts.Daemon(self.cfg))
        self.assertEqual(lines[0], "QUEUE 0 0 0 0")
        self.assertEqual([l.split("\t")[1] for l in lines[1:]], [f"re utt{n}" for n in range(102, 110)])
        self.assertTrue(all(l.split()[2] == "replied" for l in lines[1:]))

    def test_busy_and_running_drive_queue(self):
        d = self.daemon(["@busy", "run", "@running", "go"])
        s, f = self.watch(self.serve(d))
        self.assertEqual(self.next_line(f), "QUEUE 0 0 0 0")
        self.press(d, 100)
        self.assertEqual([self.next_line(f), self.next_line(f)], ["QUEUE 1 0 0 0", "QUEUE 1 1 1 0"])
        self.press(d, 200)
        self.assertEqual(self.next_line(f), "QUEUE 2 1 2 0")
        self.touch("run-utt100")
        self.assertEqual(self.next_line(f), "QUEUE 2 0 1 0")
        self.touch("go-utt100")
        self.assertRegex(self.next_line(f), r"^ANSWER [0-9a-f]{8} replied utt100\tre utt100$")
        self.assertEqual(self.next_line(f), "QUEUE 1 1 1 0")
        self.touch("run-utt200")
        self.assertEqual(self.next_line(f), "QUEUE 1 0 0 0")
        self.touch("go-utt200")
        self.assertRegex(self.next_line(f), r"^ANSWER [0-9a-f]{8} replied utt200\tre utt200$")
        self.assertEqual(self.next_line(f), "QUEUE 0 0 0 0")

    def test_status_command_lists_in_flight_items(self):
        d = self.daemon(["@busy", "go"])
        d.cfg["port"] = self.serve(d)
        t, lines = self.press(d, 100)
        queue = self.tmp / "state/queue.json"
        self.until(lambda: queue.exists() and '"busy"' in queue.read_text())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ts.status_cmd(d)
        self.assertIn("queue aabbcc: waiting 1 busy 1 ahead 1 paused 0\n", out.getvalue())
        self.assertRegex(out.getvalue(), r"\n  " + lines[2].split()[1] + r" busy \d+s\n")
        self.assertIn("ota: nothing staged", out.getvalue())
        self.touch("go-utt100")
        t.join(10)

    def test_stick_log_frame_on_watch_and_before_end(self):
        d = self.daemon()
        self.quick_watch(d, "talkstick-0.3.0", frame(b"L", b"wifi: no ap x3"))
        u = ts.read_utterance(stream(frame(b"A", b"\x01\x00"), frame(b"L", b"boot 4"), frame(b"E")))
        self.assertEqual((u.outcome, u.pcm, u.logs), ("end", b"\x01\x00", [b"boot 4"]))
        d.handle_stick(stream(frame(b"L", b"x" * 1500), frame(b"C")), lambda _: None, "x")
        rows = self.events(d, "stick_log")
        self.assertEqual(rows[0]["device"], "aabbcc")
        self.assertEqual((rows[0]["fw"], rows[0]["text"]), ("talkstick-0.3.0", "wifi: no ap x3"))
        self.assertEqual(rows[1]["text"], "x" * 1024)

    def test_stick_id_names_the_item(self):
        d = self.daemon()
        t, lines = self.press(d, 100, b"0badc0de")
        t.join(10)
        self.assertEqual(lines[1:3], ["RECV", "ID 0badc0de"])
        self.assertEqual(self.quick_watch(d)[1:], ["ANSWER 0badc0de replied utt100\tre utt100"])
        self.assertTrue(self.events(d, "utterance")[0]["utterance_id"].startswith("0badc0de"))
        t, lines = self.press(d, 200, b"NOT-HEX!")
        t.join(10)
        self.assertRegex(lines[2], r"^ID [0-9a-f]{8}$")
        self.assertNotEqual(lines[2], "ID NOT-HEX!")

    def test_resent_delivered_id_is_not_delivered_again(self):
        d = self.daemon()
        self.press(d, 100, b"0badc0de")[0].join(10)
        for daemon in (d, ts.Daemon(self.cfg)):
            t, lines = self.press(daemon, 100, b"0badc0de")
            t.join(10)
            self.assertEqual(lines[1:], ["RECV", "ID 0badc0de", "SENT utt100"])
        self.assertEqual(self.delivered(), ["utt100"])
        self.assertEqual([(r["id8"], r["state"]) for r in self.events(d, "duplicate")],
                         [("0badc0de", "delivered")] * 2)

    def test_resent_id_still_in_progress_is_busy(self):
        d = self.daemon(slow=1.0)
        t1, l1 = self.press(d, 100, b"0badc0de")
        self.until(lambda: "RECV" in l1)
        t2, l2 = self.press(d, 100, b"0badc0de")
        t2.join(10)
        self.assertEqual(l2[1:], ["RECV", "ID 0badc0de", "ERR busy"])
        t1.join(10)
        self.assertEqual(l1[-1], "REPLIED")
        self.assertEqual(self.delivered(), ["utt100"])
        self.assertEqual([r["state"] for r in self.events(d, "duplicate")], ["in_progress"])

    def test_handoff_timeout_ends_in_error_and_the_id_may_be_resent(self):
        code = ("import os, sys, time; sys.stdin.read(); d = sys.argv[1]; first = not os.path.exists(d + '/tried');"
                " open(d + '/tried', 'w').close(); time.sleep(5) if first else print('@sent', flush=True);"
                " print('ok')")
        d = self.daemon()
        d.cfg["delivery"]["command"] = {"argv": py(code) + [str(self.tmp)], "handoff_timeout": 0.3, "timeout": 20}
        t0 = time.time()
        t, lines = self.press(d, 100, b"0badc0de")
        t.join(10)
        self.assertLess(time.time() - t0, 3)
        self.assertEqual(lines[-1], "ERR not posted: the thread did not take it in 0.3 s")
        self.assertEqual(self.quick_watch(d)[1:],
                         ["ANSWER 0badc0de error utt100\tnot posted: the thread did not take it in 0.3 s"])
        t, lines = self.press(d, 100, b"0badc0de")
        t.join(10)
        self.assertEqual(lines[1:], ["RECV", "ID 0badc0de", "SENT utt100", "REPLY ok", "REPLIED"])

    def test_spoken_at_and_stderr_reason(self):
        d = self.daemon()
        d.cfg["delivery"]["command"]["argv"] = py(
            "import os, sys; print(os.environ['TALKSTICK_SPOKEN_AT'], file=sys.stderr); sys.exit(4)")
        t, lines = self.press(d, 100)
        t.join(10)
        row = self.events(d, "utterance")[0]
        self.assertEqual(lines[-1], f"ERR {int(row['t_release'])}")

    def test_paused_is_the_fourth_queue_field(self):
        d = self.daemon(["@busy", "p", "@paused", "go"])
        s, f = self.watch(self.serve(d))
        self.assertEqual(self.next_line(f), "QUEUE 0 0 0 0")
        self.press(d, 100)
        self.assertEqual([self.next_line(f), self.next_line(f)], ["QUEUE 1 0 0 0", "QUEUE 1 1 1 0"])
        self.touch("p-utt100")
        self.assertEqual(self.next_line(f), "QUEUE 1 0 0 1")
        self.touch("go-utt100")
        self.assertRegex(self.next_line(f), r"^ANSWER ")
        self.assertEqual(self.next_line(f), "QUEUE 0 0 0 0")

    def test_on_reply_unacked_skips_answers_the_stick_acked(self):
        out = self.tmp / "phone"
        d = self.daemon(on_reply_when="unacked", on_reply_grace=1.5,
                        on_reply=py(f"import sys; open({str(out)!r}, 'a').write(sys.stdin.read() + '|')"))
        s, f = self.watch(self.serve(d))
        self.next_line(f)
        self.press(d, 100)[0].join(10)
        aid = next(l for l in iter(lambda: self.next_line(f), "") if l.startswith("ANSWER")).split()[1]
        s.sendall(frame(b"K", aid.encode()))
        self.press(d, 200)[0].join(10)
        d.wait_replies(10)
        self.assertEqual(out.read_text(), "re utt200|")
        self.assertEqual([(r["rc"], r.get("decision")) for r in self.events(d, "on_reply")],
                         [("skipped", "skipped: acked by stick"), (0, "sent: not acked in 1.5 s")])

    # Over-the-air updates

    def stage(self, version=None):
        (self.tmp / "ota.key").write_text(KEY.hex() + "\n")
        (self.tmp / "app.bin").write_bytes(IMAGE)
        return ts.stage_ota(self.cfg, str(self.tmp / "app.bin"), version)

    def test_staging_writes_image_manifest_and_mac(self):
        self.daemon()
        m = self.stage()
        sha = hashlib.sha256(IMAGE).hexdigest()
        mac = hmac.new(KEY, f"talkstick-ota {sha} talkstick-0.3.1".encode(), hashlib.sha256).hexdigest()
        self.assertEqual({k: m[k] for k in ("version", "size", "sha256", "mac")},
                         {"version": "talkstick-0.3.1", "size": len(IMAGE), "sha256": sha, "mac": mac})
        self.assertEqual(json.loads((self.tmp / "state/ota/manifest.json").read_text()), m)
        self.assertEqual((self.tmp / "state/ota/image.bin").read_bytes(), IMAGE)
        self.assertEqual(self.stage("talkstick-9.9.9")["version"], "talkstick-9.9.9")
        (self.tmp / "app.bin").write_bytes(b"\x00" + IMAGE)
        with self.assertRaises(ts.StepError):
            ts.stage_ota(self.cfg, str(self.tmp / "app.bin"))

    def test_offer_only_on_version_mismatch_and_only_with_a_key(self):
        d = self.daemon()
        s, f = self.watch(self.serve(d))
        self.assertEqual(self.next_line(f), "QUEUE 0 0 0 0")
        m = self.stage()
        d.check_ota()
        offer = f"OTA {m['size']} {m['sha256']} {m['mac']} talkstick-0.3.1"
        self.assertEqual(self.next_line(f), offer)
        self.assertEqual(self.quick_watch(d, "talkstick-0.3.0"), ["QUEUE 0 0 0 0", offer])
        self.assertEqual(self.quick_watch(d, "talkstick-0.3.1"), ["QUEUE 0 0 0 0"])
        (self.tmp / "ota.key").unlink()
        self.assertEqual(self.quick_watch(d, "talkstick-0.3.0"), ["QUEUE 0 0 0 0"])
        self.assertEqual(len(self.events(d, "ota_offer")), 2)

    def test_fetch_returns_the_exact_image(self):
        d = self.daemon()
        self.stage()
        lines = []
        d.handle_stick(stream(frame(b"F", b"talkstick-0.3.1")), lines.append, "x")
        self.assertEqual(lines[1], f"IMAGE {len(IMAGE)} {hashlib.sha256(IMAGE).hexdigest()}\n".encode() + IMAGE)
        lines = []
        d.handle_stick(stream(frame(b"F", b"talkstick-0.3.2")), lines.append, "x")
        self.assertEqual(lines[1:], ["ERR no image"])
        self.assertEqual([r.get("bytes") for r in self.events(d, "ota_fetch")], [len(IMAGE), None])

    def test_ota_applied_logged_once_per_staging(self):
        d = self.daemon()
        self.stage()
        d.check_ota()
        self.quick_watch(d, "talkstick-0.3.1")
        self.quick_watch(d, "talkstick-0.3.1")
        self.assertEqual(len(self.events(d, "ota_applied")), 1)
        time.sleep(0.01)
        self.stage()
        d.check_ota()
        self.quick_watch(d, "talkstick-0.3.1")
        self.assertEqual([r["device"] for r in self.events(d, "ota_applied")], ["aabbcc", "aabbcc"])

    def ota_cli(self, *args):
        (self.tmp / "config.json").write_text(json.dumps(self.cfg))
        env = {**os.environ, "TALKSTICK_CONFIG": str(self.tmp / "config.json")}
        return subprocess.Popen([sys.executable, str(ROOT / "daemon/talkstick"), "ota", str(self.tmp / "app.bin"),
                                 *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_ota_command_waits_for_the_stick(self):
        d = self.daemon()
        (self.tmp / "app.bin").write_bytes(IMAGE)
        p = self.ota_cli("--timeout", "5")
        self.assertEqual(p.communicate(timeout=10)[1].strip().split(":")[0], "talkstick ota")
        self.assertEqual(p.returncode, 1)
        (self.tmp / "ota.key").write_text(KEY.hex())
        p = self.ota_cli("--timeout", "20")
        self.until(lambda: (self.tmp / "state/ota/manifest.json").exists())
        d.check_ota()
        self.quick_watch(d, "talkstick-0.3.1")
        out, _ = p.communicate(timeout=10)
        self.assertEqual(p.returncode, 0)
        self.assertRegex(out, r"applied on aabbcc after [\d.]+ s\n$")
        p = self.ota_cli("--timeout", "0.5", "--version", "talkstick-0.4.0")
        self.assertIn("no stick said hello with talkstick-0.4.0", p.communicate(timeout=10)[1])
        self.assertEqual(p.returncode, 1)


FAKE_T3 = """#!/usr/bin/env python3
import json, os, sys
d = os.path.dirname(os.path.abspath(__file__))
scenario = json.load(open(os.path.join(d, 'scenario.json')))
if sys.argv[1:3] == ['thread', 'send']:
    with open(os.path.join(d, 'sent.json'), 'a') as f:
        f.write(json.dumps({'argv': sys.argv[3:], 'text': sys.stdin.read()}) + '\\n')
    print(json.dumps(scenario['send']))
    sys.exit(0)
counter = os.path.join(d, 'shows')
n = int(open(counter).read()) if os.path.exists(counter) else 0
open(counter, 'w').write(str(n + 1))
print(json.dumps(scenario['shows'][min(n, len(scenario['shows']) - 1)]))
"""

OURS, OTHER = "run:thread:t:ordinal:9", "run:thread:t:ordinal:8"


def t3_state(status="running", active=None, msgs=()):
    return {"thread": {"settled": False, "archived": False, "status": status, "activeRunId": active,
                       "latestRunId": OURS}, "messages": list(msgs)}


USER = {"role": "user", "runId": OURS, "text": "[stick 12:00:00] hello"}


class T3Deliver(unittest.TestCase):
    """examples/t3-driver/deliver.py against a fake t3 CLI that walks a scripted thread."""

    def run_deliver(self, shows, spoken_ago=5.0, **env):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        d = Path(holder.name)
        (d / "t3").write_text(FAKE_T3)
        (d / "t3").chmod(0o755)
        (d / "scenario.json").write_text(json.dumps(
            {"send": {"runId": OURS, "status": "queued", "delivery": "queued"}, "shows": shows}))
        self.spoken = time.time() - spoken_ago
        p = subprocess.run([sys.executable, str(ROOT / "examples/t3-driver/deliver.py")], input="hello",
                           capture_output=True, text=True, timeout=30,
                           env={**os.environ, "T3_BIN": str(d / "t3"), "T3_THREAD": "t", "T3_POLL": "0.01",
                                "TALKSTICK_SPOKEN_AT": str(self.spoken), **env})
        sent = [json.loads(l) for l in (d / "sent.json").read_text().splitlines()] if (d / "sent.json").exists() else []
        return p, sent

    def test_queued_then_paused_then_running_then_replied(self):
        p, sent = self.run_deliver([
            t3_state(active=OTHER),
            t3_state(active=OTHER),
            t3_state("queued"),
            t3_state("queued"),
            t3_state(active=OURS, msgs=[USER]),
            t3_state("completed", msgs=[USER, {"role": "assistant", "runId": OURS, "text": " hi back "}]),
        ])
        self.assertEqual((p.returncode, p.stdout), (0, "@sent\n@busy\n@paused\n@running\nhi back\n"))
        self.assertEqual(sent[0]["argv"], ["--json", "t", "-"])

    def test_other_runs_text_is_never_the_reply(self):
        p, _ = self.run_deliver([
            t3_state(active=OTHER),
            t3_state(msgs=[{"role": "assistant", "runId": OTHER, "text": "not yours"}], active=OURS),
            t3_state("completed", msgs=[USER, {"role": "assistant", "runId": OURS, "text": "yours"}]),
        ])
        self.assertEqual(p.stdout, "@sent\n@running\nyours\n")

    def test_stopped_turn_gives_the_empty_reply_line(self):
        p, _ = self.run_deliver([t3_state(active=OTHER), t3_state(active=OURS, msgs=[USER]),
                                 t3_state("completed", msgs=[USER])])
        self.assertEqual((p.returncode, p.stdout),
                         (0, "@sent\n@running\n(the agent finished with no text reply)\n"))

    def test_queue_timeout_exits_4(self):
        p, _ = self.run_deliver([t3_state(active=OTHER)], T3_QUEUE_TIMEOUT="0.3")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (4, "@sent\n@busy\n", "still queued in T3\n"))

    def test_spoken_time_prefix(self):
        done = t3_state("completed", msgs=[USER, {"role": "assistant", "runId": OURS, "text": "ok"}])
        _, sent = self.run_deliver([t3_state(), done])
        clock = time.strftime("%H:%M:%S", time.localtime(self.spoken))
        self.assertEqual(sent[0]["text"], f"[stick {clock}] hello")
        _, sent = self.run_deliver([t3_state(), done], spoken_ago=150)
        clock = time.strftime("%H:%M:%S", time.localtime(self.spoken))
        self.assertEqual(sent[0]["text"], f"[stick {clock}, 2 min late] hello")
        _, sent = self.run_deliver([t3_state(), done], T3_PREFIX="desk")
        self.assertTrue(sent[0]["text"].startswith("[desk "))


if __name__ == "__main__":
    unittest.main()
