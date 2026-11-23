#!/bin/sh
# Runs after the configuration is filled in (20-envsubst-on-templates.sh), #768: an
# encrypted key's password is in tls.password next to it; without that file (a key
# without a password), nginx must not look for one.
set -eu
# The paths can be changed for tests only.
SSL_DIR="${SSL_DIR:-/etc/nginx/ssl}"
CONF="${CONF:-/etc/nginx/nginx.conf}"
if [ -f "$SSL_DIR/tls.password" ]; then
    exit 0
fi
sed -i '/^[[:space:]]*ssl_password_file[[:space:]]/d' "$CONF"
