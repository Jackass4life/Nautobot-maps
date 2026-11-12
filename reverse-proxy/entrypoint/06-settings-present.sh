#!/bin/sh
# The reverse-proxy image (#672): the settings it fills in at start must be there,
# or nginx would run with an empty proxy secret (the app then trusts no sign-in) or
# no identity provider.  reverse-proxy/compose.yml checks the same with ${...:?}.
set -eu
missing=""
[ -n "${AUTH_PROXY_SECRET:-}" ] || missing="$missing AUTH_PROXY_SECRET"
[ -n "${OIDC_ISSUER_URL:-}" ] || missing="$missing OIDC_ISSUER_URL"
if [ -n "$missing" ]; then
    echo "$0: set$missing (see .env.example, \"HTTPS and sign-in\")" >&2
    exit 1
fi
