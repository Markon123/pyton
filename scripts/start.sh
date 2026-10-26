#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
venv_dir="${FSP_VENV_DIR:-$project_dir/.venv}"
"$venv_dir/bin/python" manage.py check
"$venv_dir/bin/python" manage.py send_reminders
exec "$venv_dir/bin/python" manage.py runserver "${FSP_BIND:-127.0.0.1:8000}" --noreload
