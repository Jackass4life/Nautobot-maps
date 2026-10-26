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

## Permissions

In Docker the app runs as user **10001**, not as you. Give it the folder, and
keep everyone else out:

```bash
chmod 600 config/wireless.yml        # each file, while it is still yours
sudo chown -R 10001:10001 config     # then give the folder to the app
sudo chmod 700 config
```

Edit the files with `sudo` afterwards (or `sudo -u '#10001'`). If the app
can't read a file it stops at startup and says so ("can't be read:
PermissionError … uid 10001"). Without Docker (`python app.py`), point the
settings at the files wherever they are; the app then runs as you.

After changing a file, restart the app: `docker compose up -d --force-recreate nautobot-maps`.
