#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' 'update_result_site.sh now publishes the validated site to GitHub Pages.' >&2
exec "$(dirname "$0")/publish_github_pages.sh" --build "$@"
