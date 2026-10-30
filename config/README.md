# config/

The app's own files, mounted read-only into the container at `/config` by
`docker-compose.yml` (#517). Everything here except this README is
git-ignored: these files hold passwords and client secrets, so never commit
them. An empty folder is fine: nothing here turns on by itself; each file is
used only when a setting in `.env` points at it.

| File | Setting in `.env` | What it is |
|---|---|---|
| `wireless.yml` | `WIRELESS_CONTROLLERS_FILE=/config/wireless.yml` | Catalyst Center and Aruba New Central instances and their read-only accounts ([sources](../docs/sources.md#wireless-controllers-access-points)) |
| `criticality_rules.json` | `CRITICALITY_RULES_FILE=/config/criticality_rules.json` | Critical role keywords per location type (start from `../criticality_rules.json`) |
| `certs/internal-ca.pem` | `NAUTOBOT_VERIFY_SSL`, `LIBRENMS_VERIFY_SSL` or a controller's `verify_ssl` = `/config/certs/internal-ca.pem` | Your internal CA, when a server's certificate is signed by it |
| `company-ca.pem` | `OIDC_CA_FILE=./config/company-ca.pem` (a host path: the bundled proxy's oauth2-proxy reads it, not the app) | The CA of an identity provider with a company certificate ([company CA](../docs/authentication.md#company-ca)); `chmod 644`, since oauth2-proxy runs as another user |

## Permissions

In Docker the app runs as user and group **10001**, not as you. Keep owning
the folder and its files yourself (so git, the build and your editor keep
working), and let the app read them through its group:

```bash
sudo chgrp -R 10001 config   # the app's group in the container
sudo chmod 2750 config       # you: all; the app: read; others: nothing. New files get the group too
chmod 640 config/wireless.yml   # each file: you read and write, the app reads
```

A file you add later gets the group by itself (the `2` in `2750`); make sure
it is `640`, not `600` (a copy of a `600` file stays `600`). If the app can't
read the wireless controllers file it stops at startup and says what to run;
an unreadable criticality rules file or CA certificate is logged as an error
at startup (the app then uses `CRITICAL_ROLE_KEYWORDS`, or its TLS
connections fail). Without Docker (`python app.py`) the app runs as you:
nothing to do.

After changing a file, restart the app: `docker compose up -d --force-recreate nautobot-maps`.
