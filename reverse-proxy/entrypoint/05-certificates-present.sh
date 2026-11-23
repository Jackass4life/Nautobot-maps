#!/bin/sh
# Runs before nginx's own scripts (#667, #768): without the certificate nginx stops
# with only "cannot load certificate"; say where it goes instead.  Its names changed
# in #768 (fullchain.pem -> tls.cert, privkey.pem -> tls.privkey): say how to rename.
set -eu
# The path can be changed for tests only.
SSL_DIR="${SSL_DIR:-/etc/nginx/ssl}"
missing=""
for file in tls.cert tls.privkey; do
    [ -s "$SSL_DIR/$file" ] || missing="$missing $file"
done
if [ -n "$missing" ]; then
    echo "$0: missing or empty in reverse-proxy/certs/:$missing" >&2
    if [ -s "$SSL_DIR/fullchain.pem" ] || [ -s "$SSL_DIR/privkey.pem" ]; then
        echo "$0: the files were renamed in #768; in reverse-proxy/certs/:" >&2
        echo "$0:   mv fullchain.pem tls.cert && mv privkey.pem tls.privkey" >&2
    fi
    echo "$0: put your certificate there (docs/authentication.md, \"Your own certificate\")" >&2
    exit 1
fi
