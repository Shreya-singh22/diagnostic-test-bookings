#!/bin/sh
# Apply migrations and (idempotently) seed demo data, then run the given command.
set -e
if [ "${SKIP_MIGRATIONS:-false}" != "true" ]; then
  alembic upgrade head
  python -m app.seed
fi
exec "$@"
