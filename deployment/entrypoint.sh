#!/bin/sh
set -eu
cd /app
mkdir -p .local
if [ "${FSP_SKIP_MIGRATIONS:-0}" != '1' ]; then
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    python manage.py prepare_project
    if [ "${FSP_SEED_DEMO:-0}" = '1' ]; then
        python manage.py seed_full_demo
    fi
    python manage.py check
fi
exec "$@"
