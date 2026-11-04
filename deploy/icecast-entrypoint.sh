#!/bin/sh
set -eu
umask 077
config=/run/secrets/icecast_config
fail() { echo 'Icecast preflight failed: explicit valid private configuration required.' >&2; exit 78; }
[ "$(id -u)" != 0 ] || fail
[ -s "$config" ] || fail
# Managed secrets are hex-only, 256-bit values. Never invoke upstream default-password edits.
for key in source-password admin-password relay-password; do
  grep -qE "<$key>[0-9a-f]{64}</$key>" "$config" || fail
done
if grep -qE '<!DOCTYPE|<!ENTITY' "$config"; then fail; fi
xmllint --nonet --noout "$config" >/dev/null 2>&1 || fail
exec icecast -c "$config"
