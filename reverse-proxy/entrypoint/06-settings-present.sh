#!/bin/sh
# The reverse-proxy image (#672): the settings it fills in at start must be there,
# or nginx would run with an empty proxy secret (the app then trusts no sign-in) or
# no identity provider (only with oauth2-proxy: SIGN_IN=app has none, #723).
# reverse-proxy/compose.yml checks the same with ${...:?}.
set -eu
missing=""
[ -n "${AUTH_PROXY_SECRET:-}" ] || missing="$missing AUTH_PROXY_SECRET"
if [ "${SIGN_IN:-oauth2-proxy}" = "oauth2-proxy" ]; then
    [ -n "${OIDC_ISSUER_URL:-}" ] || missing="$missing OIDC_ISSUER_URL"
fi
if [ -n "$missing" ]; then
    echo "$0: set$missing (see .env.example, \"HTTPS and sign-in\")" >&2
    exit 1
fi
