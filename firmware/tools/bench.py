"""Drive the stick from the Mac over USB serial.

    python3 bench.py cmd "ptt 3" [--listen 20]      send a console command, print the stick's log
    python3 bench.py pcm speech.wav [--listen 60]   push audio into the stick; it streams it over WiFi like a press
    python3 bench.py listen 30                       print the stick's log

Needs pyserial (for example `uv run --with pyserial python3 bench.py ...`). `pcm` takes a 16 kHz mono 16-bit WAV;
anything else is converted with ffmpeg first.
"""
import argparse
import glob
import io
import subprocess
import sys
import time
import wave

import serial



def open_port(port, timeout):
    """Open the stick's USB serial without resetting it. On the ESP32-S3 USB-Serial/JTAG, RTS asserted while DTR is
    not holds the chip in reset, and pyserial's defaults leave it frozen, so set DTR on and RTS off before opening."""
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, timeout
    s.dtr, s.rts = True, False
    s.open()
    return s

def default_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("no /dev/cu.usbmodem* port, pass --port")
    return ports[0]


def pcm_from(path):
    try:
        with wave.open(path) as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2):
                return w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        pass
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        check=True, capture_output=True,
    )
    return out.stdout


def listen(s, seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        line = s.readline()
        if not line:
            continue
        text = line.decode(errors="replace").rstrip()
        print(text, flush=True)
        if until and until in text:
            return text
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["cmd", "pcm", "listen"])
    ap.add_argument("arg")
    ap.add_argument("--port", default=None)
    ap.add_argument("--listen", type=float, default=10)
    args = ap.parse_args()

    with open_port(args.port or default_port(), 0.2) as s:
        if args.action == "listen":
            listen(s, float(args.arg))
        elif args.action == "cmd":
            s.write(args.arg.encode() + b"\n")
            listen(s, args.listen)
        else:
            pcm = pcm_from(args.arg)
            s.write(f"pcm {len(pcm)}\n".encode())
            if not listen(s, 3, until="pcm ready"):
                sys.exit("stick did not answer pcm")
            # Paced: the stick's USB receive ring drops bytes if the host writes at full speed.
            for off in range(0, len(pcm), 1024):
                s.write(pcm[off:off + 1024])
                s.flush()
                time.sleep(0.004)
            print(f"pushed {len(pcm)} bytes ({len(pcm) / 32000:.2f} s)", flush=True)
            listen(s, args.listen)


if __name__ == "__main__":
    main()
