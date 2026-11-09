#!/bin/bash
# Builds the talkstick firmware.
#
#   build.sh [config.env ...]
#
# Config files are read in order (later ones win), default ~/.config/talkstick/firmware.env; see
# config.example.env for the keys. Environment variables with the same TALKSTICK_* names override the
# files. WiFi keys may also be named M5_WIFI_SSID / M5_WIFI_PASS. The generated header and the image
# both contain the WiFi password, so they go to private directories outside the repo:
#   TALKSTICK_GEN_DIR    default ~/.config/talkstick/gen
#   TALKSTICK_BUILD_DIR  default ~/.cache/talkstick/build
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
fqbn="${FQBN:-m5stack:esp32:m5stack_sticks3}"
gen="${TALKSTICK_GEN_DIR:-$HOME/.config/talkstick/gen}"
out="${TALKSTICK_BUILD_DIR:-$HOME/.cache/talkstick/build}"
[ "$#" -gt 0 ] || set -- "$HOME/.config/talkstick/firmware.env"

umask 077
mkdir -p "$gen" "$out"
chmod 700 "$gen" "$out"

/usr/bin/python3 -I - "$gen/talkstick_config.h" "$@" <<'PY'
import os, sys

header_path, env_paths = sys.argv[1], sys.argv[2:]
aliases = {"M5_WIFI_SSID": "TALKSTICK_WIFI_SSID", "M5_WIFI_PASS": "TALKSTICK_WIFI_PASS"}
values = {"TALKSTICK_SERVICE": "m5remote", "TALKSTICK_PORT": "47820", "TALKSTICK_HOST_FALLBACK": ""}

for path in env_paths:
    with open(os.path.expanduser(path)) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[aliases.get(key, key)] = value

for key in list(values) + ["TALKSTICK_WIFI_SSID", "TALKSTICK_WIFI_PASS"]:
    if os.environ.get(key):
        values[key] = os.environ[key]

missing = [k for k in ("TALKSTICK_WIFI_SSID", "TALKSTICK_WIFI_PASS") if not values.get(k)]
if missing:
    sys.exit("config is missing " + ", ".join(missing))
service = values["TALKSTICK_SERVICE"].strip().removeprefix("_").removesuffix("._tcp")
port = int(values["TALKSTICK_PORT"])

def c_string(s):
    return '"' + "".join("\\%03o" % b for b in s.encode()) + '"'

body = (
    "#pragma once\n"
    "#define TS_WIFI_SSID " + c_string(values["TALKSTICK_WIFI_SSID"]) + "\n"
    "#define TS_WIFI_PASS " + c_string(values["TALKSTICK_WIFI_PASS"]) + "\n"
    "#define TS_SERVICE " + c_string(service) + "\n"
    "#define TS_PORT " + str(port) + "\n"
    "#define TS_HOST_FALLBACK " + c_string(values["TALKSTICK_HOST_FALLBACK"]) + "\n"
)
fd = os.open(header_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(body)
os.chmod(header_path, 0o600)
print(f"config header written (service _{service}._tcp, port {port})")
PY

arduino-cli compile \
  --fqbn "$fqbn" \
  --build-path "$out" \
  --build-property "compiler.cpp.extra_flags=-I$gen" \
  "$here/talkstick"

ls -l "$out"/talkstick.ino.merged.bin
