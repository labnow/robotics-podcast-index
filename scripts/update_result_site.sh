#!/usr/bin/env bash
set -euo pipefail

deployment_env="preview"
project_name="${EDGEONE_PROJECT_NAME:-robotics-podcast-index}"
database_path="${ROBOTCAST_DB:-robotics_podcasts.db}"
output_directory="${ROBOTCAST_SITE_OUTPUT:-site-dist}"
edgeone_version="1.6.40"

usage() {
  printf '%s\n' \
    "Usage: scripts/update_result_site.sh [--preview|--production] [--project NAME]" \
    "" \
    "Rebuilds and validates the public result site, then deploys it to EdgeOne Makers." \
    "Authentication uses the EdgeOne CLI login store, or EDGEONE_API_TOKEN when set."
}

while (($#)); do
  case "$1" in
    --preview) deployment_env="preview" ;;
    --production) deployment_env="production" ;;
    --project)
      shift
      if (($# == 0)); then
        printf '%s\n' "--project requires a name" >&2
        exit 2
      fi
      project_name="$1"
      ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ ! "$project_name" =~ ^[a-z0-9]([a-z0-9-]{3,48}[a-z0-9])$ ]] || [[ "$project_name" == *--* ]]; then
  printf 'Invalid EdgeOne project name: %s\n' "$project_name" >&2
  exit 2
fi

python3 -m robotcast --db "$database_path" build-site --output "$output_directory"
python3 -m robotcast --db "$database_path" validate-site --directory "$output_directory"

deploy_command=(npx --yes "edgeone@${edgeone_version}" makers deploy "$output_directory" -n "$project_name" -e "$deployment_env")
if [[ -n "${EDGEONE_API_TOKEN:-}" ]]; then
  deploy_command+=(-t "$EDGEONE_API_TOKEN")
fi

printf 'Deploying %s to EdgeOne project %s (%s)…\n' "$output_directory" "$project_name" "$deployment_env"
"${deploy_command[@]}"
