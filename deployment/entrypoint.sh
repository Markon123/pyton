#!/bin/sh
set -eu
cd /app
mkdir -p .local
if [ "${FSP_SKIP_MIGRATIONS:-0}" != '1' ]; then
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    python manage.py prepare_project
    python manage.py bootstrap_ministry
    python manage.py check
fi
exec "$@"
