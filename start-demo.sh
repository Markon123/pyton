#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
for candidate in python3.14 python3.13 python3.12 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' >/dev/null 2>&1; then
    exec "$candidate" scripts/launch.py "$@"
  fi
done
printf '%s\n' 'Не найден Python 3.12 или новее. Установите Python с python.org и повторите запуск.' >&2
exit 1
