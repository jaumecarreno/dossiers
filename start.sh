#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

export GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-7200}"
export GUNICORN_GRACEFUL_TIMEOUT="${GUNICORN_GRACEFUL_TIMEOUT:-120}"
echo "Gunicorn timeout: ${GUNICORN_TIMEOUT}s"

echo "Starting supervisord (web + worker)..."
exec supervisord -c /app/supervisord.conf
