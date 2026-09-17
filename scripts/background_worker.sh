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
    backend="${ROBOTCAST_TRANSCRIBE_BACKEND:-openai-whisper}"
    runtime_env="${ROBOTCAST_TRANSCRIBE_ENV:-robotcast-whisper-gpu}"
    runtime_site_packages="$(conda run -n "$runtime_env" python -c \
      'import site; print(site.getsitepackages()[0])')"
    nvidia_libraries="$runtime_site_packages/nvidia/cublas/lib:$runtime_site_packages/nvidia/cudnn/lib"
    exec env CUDA_VISIBLE_DEVICES="$gpu" LD_LIBRARY_PATH="$nvidia_libraries:${LD_LIBRARY_PATH:-}" \
      conda run --no-capture-output \
      -n "$runtime_env" python -m robotcast --db "$database_path" transcribe \
      --max-episodes "${ROBOTCAST_TRANSCRIBE_BATCH:-50}" \
      --model large-v3 --device cuda --fp16 \
      --backend "$backend" --batch-size "${ROBOTCAST_TRANSCRIBE_INFERENCE_BATCH:-4}" \
      --compute-type "${ROBOTCAST_TRANSCRIBE_COMPUTE_TYPE:-float16}" \
      --worker-id "gpu-$gpu" --lease-minutes "${ROBOTCAST_TRANSCRIBE_LEASE_MINUTES:-360}" \
      --request-interval "${ROBOTCAST_REQUEST_INTERVAL:-3}"
    ;;
  classify)
    exec conda run --no-capture-output -n robotcast-whisper-gpu \
      python -m robotcast --db "$database_path" classify --all \
      --max-episodes "${ROBOTCAST_CLASSIFY_BATCH:-100}" \
      --batch-size 5 --workers "${ROBOTCAST_CLASSIFY_WORKERS:-2}"
    ;;
  release-transcription-claim)
    gpu="${instance:?GPU instance is required}"
    exec conda run --no-capture-output -n robotcast-whisper-gpu \
      python -m robotcast --db "$database_path" release-transcription-claims \
      --worker-id "gpu-$gpu"
    ;;
  *)
    printf 'Usage: %s {transcribe|classify|release-transcription-claim} [GPU]\n' "$0" >&2
    exit 2
    ;;
esac
