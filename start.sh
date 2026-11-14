#!/usr/bin/env bash
# Render start step: prepare the database, optionally load demo data, then serve.
set -o errexit
python manage.py migrate --noinput
python manage.py seed_inventory
if [ "${ALIFARIS_DEMO:-0}" = "1" ]; then
  python manage.py seed_demo
fi
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-10000}" --workers 2 --threads 2 --timeout 60
