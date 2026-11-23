# The reverse proxy (#672, #768): nginx with HTTPS and sign-in in front of the API and
# the web image.  Built from this folder:
#
#   docker build -t nautobot-maps-reverse-proxy --build-arg DOMAIN=maps.example.com \
#       --build-arg API_PORT=4200 .
#
# Build args (written into nginx.conf now, by substitute.py):
#   DOMAIN (required)          the public host name
#   API_HOST/API_PORT          the API's container (api:5000)
#   WEB_HOST/WEB_PORT          the web image's container (web:3000)
#   SIGN_IN                    oauth2-proxy (the default: company sign-in through it) or
#                              app (#723: the app's own, sign-in with AD on /auth; no
#                              oauth2-proxy, the identity headers always empty)
#   OAUTH2_PROXY               oauth2-proxy's host:port (oauth2-proxy:4180), which must run
#   RESOLVER                   the DNS server for those names (Docker's, 127.0.0.11)
# At run time (environment, filled in at start; never in the image):
#   AUTH_PROXY_SECRET, OIDC_ISSUER_URL (oauth2-proxy only), OIDC_LOGOUT_URL (may be empty)
# The certificate, mounted at run time on /etc/nginx/ssl, never in the image:
#   tls.cert, tls.privkey and, for an encrypted key, tls.password.

# The configuration: substitute.py fills in the build args; Python stays in this stage.
FROM nginx:1.28.3-alpine AS config

ARG DOMAIN
ENV DOMAIN=$DOMAIN
ARG API_HOST=api
ENV API_HOST=$API_HOST
ARG API_PORT=5000
ENV API_PORT=$API_PORT
ARG WEB_HOST=web
ENV WEB_HOST=$WEB_HOST
ARG WEB_PORT=3000
ENV WEB_PORT=$WEB_PORT
ARG OAUTH2_PROXY=oauth2-proxy:4180
ENV OAUTH2_PROXY=$OAUTH2_PROXY
ARG RESOLVER=127.0.0.11
ENV RESOLVER=$RESOLVER
ARG SIGN_IN=oauth2-proxy

RUN apk add --no-cache python3 py3-pip openssl >/dev/null

COPY substitute.py substitute.py
COPY requirements.txt requirements.txt
COPY nginx.conf proxy-to-api.inc proxy-to-web.inc /src/
COPY sign-in/ /src/sign-in/

RUN pip3 install --quiet --require-hashes --no-deps -r requirements.txt --break-system-packages
RUN test -n "$DOMAIN" || { echo "Set --build-arg DOMAIN=<the public host name>" >&2; exit 1; }
RUN case "$SIGN_IN" in oauth2-proxy|app) ;; \
      *) echo "SIGN_IN: oauth2-proxy or app, not '$SIGN_IN'" >&2; exit 1;; esac
# The sign-in parts (sign-in-*.inc) from sign-in/$SIGN_IN/; the secrets stay ${...}.
RUN mkdir -p /templates \
    && for file in /src/nginx.conf /src/proxy-to-api.inc /src/proxy-to-web.inc /src/sign-in/"$SIGN_IN"/*.inc; do \
         python3 substitute.py "$file" "/templates/$(basename "$file").template" \
           --only DOMAIN,API_HOST,API_PORT,WEB_HOST,WEB_PORT,OAUTH2_PROXY,RESOLVER || exit 1; \
       done

# nginx -t on the configuration as nginx will run it, with placeholder secrets and a
# throwaway certificate (deleted again): a mistake fails the build, not the first
# start.  `nginx -t` opens the listening sockets: on a builder without IPv6 the [::]
# ones go first, as the same script does at start (#527, #680).  IF_INET6: its test
# hook (CI).
ARG IF_INET6=/proc/net/if_inet6
COPY --chmod=755 entrypoint/30-listen-ipv6-only-if-available.sh /listen-ipv6-only-if-available.sh
RUN for template in /templates/*.template; do \
      AUTH_PROXY_SECRET=check OIDC_ISSUER_URL=https://idp.example.com OIDC_LOGOUT_URL= \
      envsubst '${AUTH_PROXY_SECRET} ${OIDC_ISSUER_URL} ${OIDC_LOGOUT_URL}' \
        < "$template" > "/etc/nginx/$(basename "$template" .template)"; \
    done \
    && IF_INET6="$IF_INET6" CONF_DIR=/etc/nginx /listen-ipv6-only-if-available.sh
RUN mkdir -p /etc/nginx/ssl
RUN openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -subj "/CN=localhost" \
    -keyout /etc/nginx/ssl/tls.privkey \
    -out /etc/nginx/ssl/tls.cert 2>/dev/null
RUN touch /etc/nginx/ssl/tls.password
RUN nginx -t
RUN rm /etc/nginx/ssl/tls.cert
RUN rm /etc/nginx/ssl/tls.privkey
RUN rm /etc/nginx/ssl/tls.password
RUN touch /templates/.config-checked

FROM nginx:1.28.3-alpine
ARG SIGN_IN=oauth2-proxy
# nginx.conf and its includes, filled in at start into /etc/nginx (nginx's
# 20-envsubst-on-templates.sh); only the secrets, nginx's own $variables stay.
COPY --from=config /templates/ /etc/nginx/templates/
RUN rm -f /etc/nginx/conf.d/default.conf && mkdir -p /etc/nginx/ssl \
    && mv /etc/nginx/templates/.config-checked /etc/nginx/.config-checked
ENV NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx \
    NGINX_ENVSUBST_FILTER="^(AUTH_PROXY_SECRET|OIDC_ISSUER_URL|OIDC_LOGOUT_URL)$" \
    OIDC_LOGOUT_URL="" \
    SIGN_IN="$SIGN_IN"
# Stops with what is missing (the certificate, a setting); no key password without
# tls.password; IPv6 only where the host has it.
COPY --chmod=755 entrypoint/05-certificates-present.sh entrypoint/06-settings-present.sh \
    entrypoint/25-key-password-if-given.sh entrypoint/30-listen-ipv6-only-if-available.sh \
    /docker-entrypoint.d/
EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]
