#!/usr/bin/env bash
set -euo pipefail

mode="${1:-status}"
case "$mode" in
  one)
    systemctl --user disable --now robotcast-transcribe@0.timer
    systemctl --user stop robotcast-transcribe@0.service
    systemctl --user reset-failed robotcast-transcribe@0.service || true
    systemctl --user enable --now robotcast-transcribe@1.timer
    systemctl --user start --no-block robotcast-transcribe@1.service
    ;;
  two)
    systemctl --user enable --now robotcast-transcribe@0.timer robotcast-transcribe@1.timer
    systemctl --user start --no-block robotcast-transcribe@0.service robotcast-transcribe@1.service
    ;;
  off)
    systemctl --user disable --now robotcast-transcribe@0.timer robotcast-transcribe@1.timer
    systemctl --user stop robotcast-transcribe@0.service robotcast-transcribe@1.service
    systemctl --user reset-failed robotcast-transcribe@0.service robotcast-transcribe@1.service || true
    ;;
  status)
    systemctl --user is-enabled robotcast-transcribe@0.timer robotcast-transcribe@1.timer || true
    systemctl --user status 'robotcast-transcribe@*.service' --no-pager || true
    ;;
  *)
    printf 'Usage: %s {one|two|off|status}\n' "$0" >&2
    exit 2
    ;;
esac
