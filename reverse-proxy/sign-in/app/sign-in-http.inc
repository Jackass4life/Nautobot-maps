# The app's own sign-in (#723, SIGN_IN=app): nothing at http level.  No oauth2-proxy;
# the identity headers stay empty (proxy-to-api.inc), so nobody can claim a user by
# sending them, and the app signs people in itself: /auth (AD), its HttpOnly cookie,
# or an API token.
