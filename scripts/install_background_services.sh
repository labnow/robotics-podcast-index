#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/robotcast"
conda_dir="$(dirname "$(command -v conda)")"
codex_dir="$(dirname "$(command -v codex)")"
mkdir -p "$unit_dir"
mkdir -p "$config_dir"
if [[ ! -e "$config_dir/robotcast.env" ]]; then
  install -m 0600 /dev/null "$config_dir/robotcast.env"
fi
install -m 0644 "$project_dir"/packaging/systemd/robotcast-*.timer "$unit_dir/"
for template in "$project_dir"/packaging/systemd/robotcast-*.service; do
  target="$unit_dir/$(basename "$template")"
  sed -e "s|@PROJECT_DIR@|$project_dir|g" \
      -e "s|@CONDA_DIR@|$conda_dir|g" \
      -e "s|@CODEX_DIR@|$codex_dir|g" "$template" > "$target"
  chmod 0644 "$target"
done
systemctl --user disable --now robotcast-transcribe.timer 2>/dev/null || true
systemctl --user stop robotcast-transcribe.service 2>/dev/null || true
rm -f "$unit_dir/robotcast-transcribe.timer" "$unit_dir/robotcast-transcribe.service"
systemctl --user daemon-reload
systemctl --user enable --now robotcast-transcribe@1.timer robotcast-classify.timer \
  robotcast-main-refresh.timer robotcast-main-publish.timer
systemctl --user list-timers 'robotcast-*' --no-pager

printf '%s\n' \
  'Workers installed. Useful commands:' \
  '  systemctl --user status robotcast-transcribe@1.timer robotcast-classify.timer' \
  '  Enable a second card with: systemctl --user enable --now robotcast-transcribe@0.timer' \
  '  scripts/robotcast_service.sh {refresh|publish|status|logs}' \
  '  Set ROBOTCAST_PAGES_REMOTE in ~/.config/robotcast/robotcast.env to publish.' \
  '  journalctl --user -u robotcast-transcribe@1.service -f' \
  '  journalctl --user -u robotcast-classify.service -f'
