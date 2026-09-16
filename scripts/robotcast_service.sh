#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  refresh) systemctl --user start robotcast-main@refresh.service ;;
  publish) systemctl --user start robotcast-main@publish.service ;;
  status)
    systemctl --user status 'robotcast-main@*' robotcast-classify.service \
      'robotcast-transcribe@*.service' --no-pager
    ;;
  logs)
    journalctl --user -u 'robotcast-main@*' -u robotcast-classify.service \
      -u 'robotcast-transcribe@*.service' -f
    ;;
  *) printf 'Usage: %s {refresh|publish|status|logs}\n' "$0" >&2; exit 2 ;;
esac
