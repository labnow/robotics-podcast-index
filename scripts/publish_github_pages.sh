#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
database_path="${ROBOTCAST_DB:-$project_dir/robotics_podcasts.db}"
site_output="${ROBOTCAST_SITE_OUTPUT:-$project_dir/site-dist}"
pages_remote="${ROBOTCAST_PAGES_REMOTE:-}"
build=0
python_runner=(conda run --no-capture-output -n robotcast-whisper-gpu python)

usage() {
  printf '%s\n' \
    'Usage: scripts/publish_github_pages.sh [--build] [--remote GITHUB_URL]' \
    '       [--database PATH] [--site PATH]' \
    '' \
    'Builds/validates the static site and publishes it to the gh-pages branch.'
}

while (($#)); do
  case "$1" in
    --build) build=1 ;;
    --remote) shift; pages_remote="${1:?--remote requires a URL}" ;;
    --database) shift; database_path="${1:?--database requires a path}" ;;
    --site) shift; site_output="${1:?--site requires a path}" ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

cd "$project_dir"
if ((build)); then
  "${python_runner[@]}" -m robotcast --db "$database_path" build-site --output "$site_output"
fi
"${python_runner[@]}" -m robotcast validate-site --directory "$site_output"

if [[ -z "$pages_remote" ]]; then
  printf '%s\n' 'Site validated; publication skipped because ROBOTCAST_PAGES_REMOTE is unset.'
  exit 0
fi
if [[ ! "$pages_remote" =~ ^(https://github\.com/|git@github\.com:)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]]; then
  printf 'Refusing non-GitHub Pages remote: %s\n' "$pages_remote" >&2
  exit 2
fi

publish_tmp="$(mktemp -d /tmp/robotcast-pages.XXXXXX)"
trap 'rm -rf "$publish_tmp"' EXIT
checkout="$publish_tmp/repository"
if git ls-remote --exit-code --heads "$pages_remote" gh-pages >/dev/null 2>&1; then
  git clone --quiet --depth 1 --branch gh-pages "$pages_remote" "$checkout"
else
  git clone --quiet --depth 1 "$pages_remote" "$checkout"
  git -C "$checkout" switch --orphan gh-pages
  git -C "$checkout" rm -rf --ignore-unmatch .
fi
rsync -a --delete --exclude=.git "$site_output/" "$checkout/"
git -C "$checkout" add --all
if git -C "$checkout" diff --cached --quiet; then
  printf '%s\n' 'GitHub Pages is already current.'
  exit 0
fi
git -C "$checkout" -c user.name="${ROBOTCAST_GIT_NAME:-Robotcast Automation}" \
  -c user.email="${ROBOTCAST_GIT_EMAIL:-robotcast@localhost}" \
  commit --quiet -m "Update podcast index $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git -C "$checkout" push origin HEAD:gh-pages
printf '%s\n' 'Published validated site to GitHub Pages branch gh-pages.'
