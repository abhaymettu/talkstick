"""Run: python3 -m unittest discover -s tests"""
import importlib.machinery
import importlib.util
import io
import json
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
        self.assertRegex(lines[0], r"^TIME \d+ -?\d+$")
        self.assertEqual(lines[1:], ["RECV", "SENT turn on the lights", "REPLY done", "REPLIED"])
        row = events[-1]
        self.assertEqual(row["outcome"], "replied")
        for k in ("d_transcript", "d_sent", "d_reply", "d_done"):
            self.assertIn(k, row)

    def test_failing_on_reply_keeps_the_reply(self):
        d, _ = self.daemon("print('done')", on_reply=py("import sys; sys.exit(1)"))
        lines, events = self.run_stick(d)
        self.assertEqual(lines[-2:], ["REPLY done", "REPLIED"])
        row = [e for e in events if e.get("event") == "utterance"][-1]
        self.assertEqual((row["outcome"], row["on_reply_rc"]), ("replied", 1))

    def test_on_reply_timeout_is_logged_not_raised(self):
        d, _ = self.daemon("print('done')", on_reply=py("import time; time.sleep(5)"), on_reply_timeout=0.2)
        lines, events = self.run_stick(d)
        self.assertEqual(lines[-1], "REPLIED")
        row = [e for e in events if e.get("event") == "utterance"][-1]
        self.assertEqual(row["on_reply_rc"], "failed")
        self.assertIn("TimeoutExpired", row["on_reply_error"])

    def test_offline(self):
        d, _ = self.daemon("import sys; sys.exit(3)")
        lines, events = self.run_stick(d)
        self.assertEqual(lines[1:], ["RECV", "OFFLINE"])
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
        self.assertEqual(lines[1:], ["RECV", "SENT what is running", "REPLY status of: what is running", "REPLIED"])
        row = json.loads(d.events.read_text().splitlines()[-1])
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


if __name__ == "__main__":
    unittest.main()
