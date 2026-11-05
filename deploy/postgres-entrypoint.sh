#!/bin/sh
set -eu
fail() { echo 'PostgreSQL preflight refused: check private secrets and bootstrap completion.' >&2; exit 78; }
[ "$(id -u)" != 0 ] || fail
secret_root=${ATLAS_DB_SECRETS:-/run/secrets}
for role in admin app migrator; do
  file=$secret_root/postgres_${role}_password
  [ -f "$file" ] && [ ! -L "$file" ] || fail
  [ "$(stat -c %u "$file")" = "$(id -u)" ] || fail
  case $(stat -c %a "$file") in 400|600) ;; *) fail;; esac
  [ "$(wc -c < "$file")" -le 65 ] || fail
  grep -qxE '[0-9a-f]{64}' "$file" || fail
done
if [ -f "$PGDATA/PG_VERSION" ] && [ ! -f "$PGDATA/atlas-bootstrap-complete" ]; then fail; fi
exec /usr/local/bin/docker-entrypoint.sh "$@"
