# The reverse proxy on its own (#672): nginx with HTTPS and company sign-in in front of
# the API and the web image.  Built from this folder:
#
#   docker build -t nautobot-maps-reverse-proxy --build-arg DOMAIN=maps.example.com \
#       --build-arg API_PORT=4200 .
#
# Build args (written into the configuration now):
#   DOMAIN (required)          the public host name
#   API_HOST/API_PORT          the API's container (api:5000)
#   WEB_HOST/WEB_PORT          the web image's container (web:3000)
#   OAUTH2_PROXY               oauth2-proxy's host:port (oauth2-proxy:4180), which must run
#   RESOLVER                   the DNS server for those names (Docker's, 127.0.0.11)
# At run time (environment, filled in at start; never in the image):
#   AUTH_PROXY_SECRET, OIDC_ISSUER_URL, OIDC_LOGOUT_URL (may be empty)
# The certificate: certs/fullchain.pem and certs/privkey.pem, copied in when they are
# there at build time (INCLUDE_CERTS=false: never); a volume on /etc/nginx/certs
# replaces them.  An image with the key in it belongs in a private registry only.
FROM nginx:1.28.0-alpine AS config
ARG DOMAIN
ARG API_HOST=api
ARG API_PORT=5000
ARG WEB_HOST=web
ARG WEB_PORT=3000
ARG OAUTH2_PROXY=oauth2-proxy:4180
ARG RESOLVER=127.0.0.11
RUN test -n "$DOMAIN" || { echo "Set --build-arg DOMAIN=<the public host name>" >&2; exit 1; }
COPY templates/ /src/
# The build args now; the secrets stay ${...} for nginx's entrypoint at start.
RUN mkdir -p /templates \
    && for template in /src/*.template; do \
         PUBLIC_HOSTNAME="$DOMAIN" \
         API_UPSTREAM="http://$API_HOST:$API_PORT" \
         WEB_UPSTREAM="http://$WEB_HOST:$WEB_PORT" \
         OAUTH2_PROXY_UPSTREAM="http://$OAUTH2_PROXY" \
         RESOLVER="$RESOLVER" \
         envsubst '${PUBLIC_HOSTNAME} ${API_UPSTREAM} ${WEB_UPSTREAM} ${OAUTH2_PROXY_UPSTREAM} ${RESOLVER}' \
           < "$template" > "/templates/$(basename "$template")"; \
       done

# The configuration as nginx will run it, with placeholder secrets and a throwaway
# certificate: `nginx -t` fails the build on a mistake, not the first start.  `nginx -t`
# opens the listening sockets: on a builder without IPv6, the [::] ones go first, as
# the same script does at start (#527, #680).  IF_INET6: its test hook (CI).
FROM config AS check
ARG IF_INET6=/proc/net/if_inet6
COPY --chmod=755 entrypoint/30-listen-ipv6-only-if-available.sh /listen-ipv6-only-if-available.sh
RUN apk add --no-cache openssl >/dev/null \
    && mkdir -p /check/conf.d /check/certs \
    && for template in /templates/*.template; do \
         AUTH_PROXY_SECRET=check OIDC_ISSUER_URL=https://idp.example.com OIDC_LOGOUT_URL= \
         envsubst '${AUTH_PROXY_SECRET} ${OIDC_ISSUER_URL} ${OIDC_LOGOUT_URL}' \
           < "$template" > "/check/conf.d/$(basename "$template" .template)"; \
       done \
    && sed -i 's#/etc/nginx/conf.d/#/check/conf.d/#g; s#/etc/nginx/certs/#/check/certs/#g' /check/conf.d/* \
    && IF_INET6="$IF_INET6" CONF_DIR=/check/conf.d /listen-ipv6-only-if-available.sh \
    && openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=check" \
         -keyout /check/certs/privkey.pem -out /check/certs/fullchain.pem 2>/dev/null \
    && printf 'events {}\nhttp { include /etc/nginx/mime.types; include /check/conf.d/default.conf; }\n' \
         > /check/nginx.conf \
    && nginx -t -c /check/nginx.conf \
    && touch /check/ok

# The certificate files, when they are to be in the image.
FROM config AS certs
ARG INCLUDE_CERTS=true
COPY certs/ /in/
RUN mkdir -p /out \
    && if [ "$INCLUDE_CERTS" = "true" ]; then \
         for file in fullchain.pem privkey.pem; do \
           if [ -f "/in/$file" ]; then cp "/in/$file" /out/; fi; \
         done; \
       fi

FROM nginx:1.28.0-alpine
COPY --from=config /templates/ /etc/nginx/templates/
COPY --from=check /check/ok /etc/nginx/.config-checked
# Only the secrets are filled in at start; nginx's own $variables stay.
ENV NGINX_ENVSUBST_FILTER="^(AUTH_PROXY_SECRET|OIDC_ISSUER_URL|OIDC_LOGOUT_URL)$" \
    OIDC_LOGOUT_URL=""
# Stops with what is missing (the certificate, a setting); IPv6 only where the host has it.
COPY --chmod=755 entrypoint/05-certificates-present.sh entrypoint/06-settings-present.sh \
    entrypoint/30-listen-ipv6-only-if-available.sh /docker-entrypoint.d/
COPY --from=certs /out/ /etc/nginx/certs/
EXPOSE 80 443
