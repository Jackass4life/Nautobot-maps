#!/usr/bin/env bash
# development/entrypoint.sh
#
# Initialises a fresh Nautobot instance for development / CI use:
#   1. Wait for PostgreSQL
#   2. Run database migrations
#   3. Create admin superuser + API token
#   4. Start the Django development server (HTTP, port 8080)
#
# Environment variables (all have sensible defaults for dev):
#   DJANGO_SUPERUSER_PASSWORD  – admin password          (default: admin)
#   NAUTOBOT_DB_HOST           – PostgreSQL hostname      (default: postgres)
#   NAUTOBOT_DB_PORT           – PostgreSQL port          (default: 5432)

set -euo pipefail

echo "▶ Waiting for PostgreSQL at ${NAUTOBOT_DB_HOST:-postgres}:${NAUTOBOT_DB_PORT:-5432} …"
until python -c "
import socket, sys, os
s = socket.socket()
try:
    s.connect((os.environ.get('NAUTOBOT_DB_HOST', 'postgres'),
               int(os.environ.get('NAUTOBOT_DB_PORT', 5432))))
except OSError:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; do
  sleep 2
done
echo "✔ PostgreSQL is accepting connections"

echo "▶ Running database migrations …"
nautobot-server post_upgrade --no-input 2>&1
echo "✔ Migrations complete"

echo "▶ Creating superuser + API token …"
nautobot-server createsuperuser --no-input \
    --username admin --email admin@example.com 2>/dev/null || true

# Set password and create a deterministic API token via the Django ORM.
nautobot-server shell --command "
from django.contrib.auth import get_user_model
from nautobot.users.models import Token

User = get_user_model()
user = User.objects.get(username='admin')
user.set_password('${DJANGO_SUPERUSER_PASSWORD:-admin}')
user.save()

token, created = Token.objects.get_or_create(
    user=user, key='aaaa-bbbb-cccc-dddd-eeee',
)
status = 'created' if created else 'already exists'
print(f'API token {status}: {token.key}')
"
echo "✔ Superuser ready  (admin / ${DJANGO_SUPERUSER_PASSWORD:-admin})"

echo "▶ Starting Nautobot dev server on 0.0.0.0:8080 …"
exec nautobot-server runserver 0.0.0.0:8080 --insecure
