# The status API (#527): staff signed in get their identity, anyone else goes on
# as anonymous; never the login.
auth_request /oauth2/auth-optional;
auth_request_set $auth_user $upstream_http_x_auth_request_preferred_username;
auth_request_set $auth_email $upstream_http_x_auth_request_email;
auth_request_set $auth_groups $upstream_http_x_auth_request_groups;
# A refreshed session cookie goes back to the browser (#727): oauth2-proxy renews
# the session once an hour, and the old cookie can't be renewed again.
auth_request_set $auth_cookie $upstream_http_set_cookie;
add_header Set-Cookie $auth_cookie;
# A location's add_header replaces the server's: HSTS again.
add_header Strict-Transport-Security "max-age=31536000" always;
