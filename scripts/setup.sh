#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
venv_dir="${FSP_VENV_DIR:-$project_dir/.venv}"
if [ ! -x "$venv_dir/bin/python" ]; then
  python3 -m venv "$venv_dir"
fi
"$venv_dir/bin/python" -m pip install --disable-pip-version-check -r requirements.txt
"$venv_dir/bin/python" manage.py migrate --noinput
"$venv_dir/bin/python" manage.py prepare_project
if [ "${FSP_SEED_DEMO:-0}" = '1' ]; then
  "$venv_dir/bin/python" manage.py seed_full_demo
fi
"$venv_dir/bin/python" manage.py check
