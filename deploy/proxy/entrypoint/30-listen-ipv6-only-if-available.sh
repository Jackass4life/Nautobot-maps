#!/bin/sh
# Runs after the templates are filled in (20-envsubst-on-templates.sh), #527:
# on a host (or Docker) without IPv6, nginx can't listen on [::] and stops,
# so drop those listen lines there.  With IPv6, nothing changes.
set -eu
# The paths can be changed for tests only.
IF_INET6="${IF_INET6:-/proc/net/if_inet6}"
CONF_DIR="${CONF_DIR:-/etc/nginx/conf.d}"
# No file (IPv6 off in the kernel), or no address in it (off in Docker).
if grep -q . "$IF_INET6" 2>/dev/null; then
    exit 0
fi
for conf in "$CONF_DIR"/*.conf; do
    [ -f "$conf" ] || continue
    sed -i '/^[[:space:]]*listen[[:space:]]*\[::\]/d' "$conf"
done
echo "$0: no IPv6 on this host: listening on IPv4 only"
