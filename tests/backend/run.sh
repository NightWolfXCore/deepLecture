#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
compose() {
  docker compose -p deeplecture-test --env-file tests/backend/test.env \
    -f infra/compose.yml -f tests/backend/compose.test.yml "$@"
}
cleanup() { compose down -v --remove-orphans; }
trap cleanup EXIT INT TERM
if [ "${SKIP_BUILD:-0}" = "1" ]; then
  compose up -d --no-build mysql php search nginx
else
  compose build php search test-runner
  compose up -d --no-build mysql php search nginx
fi
compose run --rm -v "$PWD/tests:/app/tests:ro" php php /app/tests/backend/php/unit.php
compose run --rm test-runner
