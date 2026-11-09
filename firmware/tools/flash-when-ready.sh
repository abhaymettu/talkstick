#!/bin/bash
# Waits until an ESP32-S3 in ROM download mode answers on USB, flashes the talkstick image, reboots it, and
# optionally runs a hook command. Useful when the stick has to be put in download mode by hand (hold the power
# button until the green LED blinks) and you are not at the keyboard when that happens.
#
#   flash-when-ready.sh [--image FILE] [--interval SECONDS] [--hook "command"]
#
# The probe uses --before no-reset, so a stick running its normal firmware is never reset by the polling.
# Exits 0 after a verified flash; the hook gets the port as $1.
set -uo pipefail

image="${TALKSTICK_BUILD_DIR:-$HOME/.cache/talkstick/build}/talkstick.ino.merged.bin"
interval=20
hook=""
while [ $# -gt 0 ]; do
  case "$1" in
    --image) image="$2"; shift 2 ;;
    --interval) interval="$2"; shift 2 ;;
    --hook) hook="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -f "$image" ] || { echo "no image at $image, run build.sh first" >&2; exit 1; }
command -v esptool >/dev/null || { echo "esptool not on PATH" >&2; exit 1; }

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
log "waiting for a stick in download mode (image $(basename "$image"), every ${interval}s)"
last_state=""
while true; do
  port="$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)"
  if [ -z "$port" ]; then
    state="no port"
  elif esptool --chip esp32s3 --port "$port" --before no-reset --after no-reset chip-id >/dev/null 2>&1; then
    log "stick answers on $port, flashing"
    if esptool --chip esp32s3 --port "$port" --baud 921600 --before no-reset --after hard-reset \
        write-flash 0 "$image"; then
      log "flashed and reset"
      if [ -n "$hook" ]; then
        sleep 8
        port="$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)"
        log "running hook"
        bash -c "$hook" _ "$port"
        log "hook exit $?"
      fi
      exit 0
    fi
    state="flash failed"
  else
    state="port $port present, not in download mode"
  fi
  [ "$state" != "$last_state" ] && log "$state"
  last_state="$state"
  sleep "$interval"
done
