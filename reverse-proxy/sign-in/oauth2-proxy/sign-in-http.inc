# Sign-in through oauth2-proxy (#423), http level: included by nginx.conf.

# An API token (LibreNMS, Alertmanager, scripts, MCP) skips the login: the app
# checks it.  Matched on the header, so no path is open without one.
map $http_authorization $api_token {
    "~*^Bearer\s+nmt_" 1;
    default 0;
}

# Where Sign out ends up (#546): the identity provider's logout, so its
# session ends too; without one (GitLab has none), a page that says so, not
# the app, where the identity provider would sign the user straight back in.
map "${OIDC_LOGOUT_URL}" $sign_out_next {
    "" /signed-out;
    default "${OIDC_LOGOUT_URL}";
}
