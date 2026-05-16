#!/bin/sh
set -e

echo "Applying database migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput --clear 2>&1 | tail -2

echo "Starting gunicorn..."
exec "$@"
