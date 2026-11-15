# Every other location: signed in, or an API token; otherwise the login.
auth_request /oauth2/auth;
auth_request_set $auth_user $upstream_http_x_auth_request_preferred_username;
auth_request_set $auth_email $upstream_http_x_auth_request_email;
auth_request_set $auth_groups $upstream_http_x_auth_request_groups;
# A refreshed session cookie goes back to the browser.
auth_request_set $auth_cookie $upstream_http_set_cookie;
add_header Set-Cookie $auth_cookie;
# A location's add_header replaces the server's: HSTS again.
add_header Strict-Transport-Security "max-age=31536000" always;
error_page 401 = @login;
