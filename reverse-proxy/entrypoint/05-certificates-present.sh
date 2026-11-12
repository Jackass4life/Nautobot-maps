#!/bin/sh
# Runs before nginx's own scripts (#667): without the certificate nginx stops
# with only "cannot load certificate"; say where it goes instead (it moved from
# deploy/proxy/certs/ to reverse-proxy/certs/).
set -eu
# The path can be changed for tests only.
CERT_DIR="${CERT_DIR:-/etc/nginx/certs}"
missing=""
for file in fullchain.pem privkey.pem; do
    [ -s "$CERT_DIR/$file" ] || missing="$missing $file"
done
if [ -n "$missing" ]; then
    echo "$0: missing or empty in reverse-proxy/certs/:$missing" >&2
    echo "$0: put your certificate there (docs/authentication.md, \"Your own certificate\");" >&2
    echo "$0: before #667 it was in deploy/proxy/certs/: mv deploy/proxy/certs/*.pem reverse-proxy/certs/" >&2
    exit 1
fi
