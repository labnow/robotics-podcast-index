#!/usr/bin/env bash
set -euo pipefail

database_path="${ROBOTCAST_DB:-robotics_podcasts.db}"
site_output="${ROBOTCAST_SITE_OUTPUT:-site-dist}"
state_path="${ROBOTCAST_UPDATE_STATE:-.robotcast/manual-update.state}"
request_interval="${ROBOTCAST_REQUEST_INTERVAL:-3}"
classify_limit="${ROBOTCAST_CLASSIFY_LIMIT:-100}"
with_quality=0
with_transcription=0
python_runner=(conda run --no-capture-output -n robotcast-whisper-gpu python)

usage() {
  printf '%s\n' \
    "Usage: scripts/manual_update.sh [--with-quality] [--with-transcription]" \
    "" \
    "Runs the auth-less update pipeline with durable stage checkpoints." \
    "A failed invocation resumes at its first unfinished stage." \
    "GitHub Pages publication is intentionally excluded."
}

while (($#)); do
  case "$1" in
    --with-quality) with_quality=1 ;;
    --with-transcription) with_transcription=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$(dirname "$state_path")"
if [[ -f "$state_path" ]] && grep -qx 'validate' "$state_path"; then
  mv "$state_path" "${state_path}.previous"
fi
touch "$state_path"

done_stage() { grep -qx "$1" "$state_path"; }
run_stage() {
  local stage="$1"
  shift
  if done_stage "$stage"; then
    printf 'Skipping completed stage: %s\n' "$stage"
    return
  fi
  printf 'Running stage: %s\n' "$stage"
  "$@"
  printf '%s\n' "$stage" >> "$state_path"
}

run_stage apple_discovery "${python_runner[@]}" -m robotcast --db "$database_path" collect \
  --page-size 200 --request-interval "$request_interval"
run_stage rss_sync "${python_runner[@]}" -m robotcast --db "$database_path" sync-feeds \
  --max-feeds 10000 --max-episodes-per-feed 10000 --request-interval "$request_interval"
run_stage public_enrichment "${python_runner[@]}" -m robotcast --db "$database_path" refresh-public \
  --max-episodes 100 --request-interval "$request_interval"
run_stage classification "${python_runner[@]}" -m robotcast --db "$database_path" classify \
  --all --max-episodes "$classify_limit" --batch-size 5 --workers 2
run_stage publisher_transcripts "${python_runner[@]}" -m robotcast --db "$database_path" fetch-transcripts \
  --max-episodes 100 --request-interval "$request_interval"

if ((with_quality)); then
  run_stage quality "${python_runner[@]}" -m robotcast --db "$database_path" score-quality \
    --all --max-episodes 100 --batch-size 5
fi
if ((with_transcription)); then
  run_stage transcription env CUDA_VISIBLE_DEVICES=1 conda run --no-capture-output \
    -n robotcast-whisper-gpu python -m robotcast --db "$database_path" transcribe \
    --max-episodes 1 --model large-v3 --device cuda --fp16 \
    --request-interval "$request_interval"
fi

run_stage site_build "${python_runner[@]}" -m robotcast --db "$database_path" build-site --output "$site_output"
run_stage validate "${python_runner[@]}" -m robotcast --db "$database_path" validate-site --directory "$site_output"
printf 'Manual update complete. Publish separately with scripts/update_result_site.sh.\n'
