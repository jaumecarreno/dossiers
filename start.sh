#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting supervisord (web + worker)..."
exec supervisord -c /app/supervisord.conf
