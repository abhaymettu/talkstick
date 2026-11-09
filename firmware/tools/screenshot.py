"""Save the stick's current screen as a PNG.

    python3 screenshot.py out.png [--port /dev/cu.usbmodemXXXX] [--scale 2]

Needs pyserial (for example `uv run --with pyserial python3 screenshot.py out.png`).
The firmware answers the serial command `shot` with a header line, raw RGB565 big-endian pixels, and `SHOT END`.
"""
import argparse
import glob
import struct
import sys
import time
import zlib

import serial


def default_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("no /dev/cu.usbmodem* port, pass --port")
    return ports[0]


def grab(port):
    with serial.Serial(port, 115200, timeout=0.5) as s:
        s.reset_input_buffer()
        s.write(b"shot\n")
        deadline = time.time() + 5
        header = None
        while time.time() < deadline:
            line = s.readline()
            if line.startswith(b"SHOT ") and b"END" not in line:
                header = line.split()
                break
        if not header:
            sys.exit("no SHOT header from the stick")
        width, height, fmt, size = int(header[1]), int(header[2]), header[3].decode(), int(header[4])
        if fmt != "RGB565BE":
            sys.exit(f"unknown pixel format {fmt}")
        data = b""
        while len(data) < size and time.time() < deadline + 5:
            data += s.read(size - len(data))
        if len(data) != size:
            sys.exit(f"short read {len(data)} of {size}")
        return width, height, data


def to_rgb_rows(width, height, data, scale):
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            v = (data[2 * (y * width + x)] << 8) | data[2 * (y * width + x) + 1]
            r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
            px = bytes(((r * 527 + 23) >> 6, (g * 259 + 33) >> 6, (b * 527 + 23) >> 6))
            row += px * scale
        rows.extend([bytes(row)] * scale)
    return rows


def write_png(path, width, height, rows):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + r for r in rows)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--port", default=None)
    ap.add_argument("--scale", type=int, default=2)
    args = ap.parse_args()
    width, height, data = grab(args.port or default_port())
    write_png(args.out, width * args.scale, height * args.scale, to_rgb_rows(width, height, data, args.scale))
    print(f"{args.out} {width}x{height} x{args.scale}")


if __name__ == "__main__":
    main()
