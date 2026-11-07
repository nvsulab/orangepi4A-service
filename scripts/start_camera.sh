#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/.." && pwd)"
exec /usr/local/bin/mjpg_streamer -i "input_uvc.so -d /dev/video1 -r 640x480 -f 30" -o "output_http.so -w $PROJECT_DIR/camera/www -p 8080"
