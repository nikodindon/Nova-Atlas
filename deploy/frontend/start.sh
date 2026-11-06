#!/bin/sh
set -eu
case "${ATLAS_PUBLIC_SCHEME:-}" in http|https) ;; *) echo 'Invalid public scheme' >&2; exit 78;; esac
# Only this operator-controlled enum is substituted, never nginx request variables.
envsubst '${ATLAS_PUBLIC_SCHEME}' < /etc/nginx/nginx.conf > /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
