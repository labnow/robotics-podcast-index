#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
database_path="${ROBOTCAST_DB:-$project_dir/robotics_podcasts.db}"
worker="${1:-}"
instance="${2:-}"
cd "$project_dir"

case "$worker" in
  transcribe)
    gpu="${instance:-1}"
    exec env CUDA_VISIBLE_DEVICES="$gpu" conda run --no-capture-output \
      -n robotcast-whisper-gpu python -m robotcast --db "$database_path" transcribe \
      --max-episodes "${ROBOTCAST_TRANSCRIBE_BATCH:-50}" \
      --model large-v3 --device cuda --fp16 \
      --worker-id "gpu-$gpu" --lease-minutes "${ROBOTCAST_TRANSCRIBE_LEASE_MINUTES:-360}" \
      --request-interval "${ROBOTCAST_REQUEST_INTERVAL:-3}"
    ;;
  classify)
    exec conda run --no-capture-output -n robotcast-whisper-gpu \
      python -m robotcast --db "$database_path" classify --all \
      --max-episodes "${ROBOTCAST_CLASSIFY_BATCH:-100}" \
      --batch-size 5 --workers "${ROBOTCAST_CLASSIFY_WORKERS:-2}"
    ;;
  *)
    printf 'Usage: %s {transcribe|classify}\n' "$0" >&2
    exit 2
    ;;
esac
