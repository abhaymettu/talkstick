#!/bin/bash
# Flashes the image built by build.sh.
#
#   PORT=/dev/cu.usbmodemXXXX flash.sh
#
# If the stick does not enter the bootloader, hold its power button until the green LED blinks
# (ROM download mode) and rerun.
set -euo pipefail

port="${PORT:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)}"
image="${TALKSTICK_BUILD_DIR:-$HOME/.cache/talkstick/build}/talkstick.ino.merged.bin"

[ -n "$port" ] || { echo "no /dev/cu.usbmodem* port found, set PORT" >&2; exit 1; }
[ -f "$image" ] || { echo "no image at $image, run build.sh first" >&2; exit 1; }
esptool --chip esp32s3 --port "$port" --baud 921600 \
  --before default-reset --after watchdog-reset \
  write-flash 0 "$image"
