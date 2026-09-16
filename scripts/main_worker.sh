#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
database_path="${ROBOTCAST_DB:-$project_dir/robotics_podcasts.db}"
site_output="${ROBOTCAST_SITE_OUTPUT:-$project_dir/site-dist}"
python_runner=(conda run --no-capture-output -n robotcast-whisper-gpu python)
mode="${1:-}"
cd "$project_dir"

case "$mode" in
  refresh)
    exec 9>"$project_dir/.robotcast/main-refresh.lock"
    flock -n 9 || { printf '%s\n' 'A main refresh is already running'; exit 0; }
    "${python_runner[@]}" -m robotcast --db "$database_path" collect \
      --page-size 200 --request-interval "${ROBOTCAST_REQUEST_INTERVAL:-3}"
    "${python_runner[@]}" -m robotcast --db "$database_path" sync-feeds \
      --max-feeds 10000 --max-episodes-per-feed 10000 \
      --request-interval "${ROBOTCAST_REQUEST_INTERVAL:-3}"
    "${python_runner[@]}" -m robotcast --db "$database_path" refresh-public \
      --max-episodes "${ROBOTCAST_PUBLIC_REFRESH_BATCH:-500}" \
      --request-interval "${ROBOTCAST_REQUEST_INTERVAL:-3}"
    "${python_runner[@]}" -m robotcast --db "$database_path" fetch-transcripts \
      --max-episodes "${ROBOTCAST_PUBLISHER_TRANSCRIPT_BATCH:-500}" \
      --request-interval "${ROBOTCAST_REQUEST_INTERVAL:-3}"
    systemctl --user start --no-block robotcast-classify.service
    systemctl --user start --no-block robotcast-transcribe.service
    ;;
  publish)
    exec 9>"$project_dir/.robotcast/main-publish.lock"
    flock -n 9 || { printf '%s\n' 'A main publication run is already active'; exit 0; }
    "${python_runner[@]}" -m robotcast --db "$database_path" score-quality \
      --all --transcripts-only --max-episodes "${ROBOTCAST_QUALITY_BATCH:-100}" \
      --batch-size 5
    "$project_dir/scripts/publish_github_pages.sh" --build \
      --database "$database_path" --site "$site_output"
    ;;
  *)
    printf 'Usage: %s {refresh|publish}\n' "$0" >&2
    exit 2
    ;;
esac
