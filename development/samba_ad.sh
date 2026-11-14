#!/usr/bin/env bash
# A real Active Directory for AD sign-in's live test (#717): a Samba AD domain
# controller, NMAPS.TEST, on 127.0.0.1 with LDAPS (636) and its own CA, and three
# accounts: svc-maps (the read-only service account), jdoe (in noc-duty) and bob.
# Test data only: the passwords below are for this throwaway domain.
#
#   sudo apt-get install -y samba samba-ad-dc   # once
#   development/samba_ad.sh start               # provisions on first use, runs in the background
#   development/samba_ad.sh stop
#
# Then tests/test_ad_live.py with AD_LIVE_URL=ldaps://dc1.nmaps.test and
# AD_LIVE_CA_FILE=$DIR/ca.pem. A short directory: Samba's Unix sockets must stay
# under 108 characters.
set -euo pipefail

DIR="${SAMBA_AD_DIR:-/tmp/nms-dc}"
CONF="$DIR/etc/smb.conf"
ADMIN_PASSWORD='Adm1n-Pass-2026!'

provision() {
  sudo rm -rf "$DIR"
  mkdir -p "$DIR"
  # xattr_tdb: container file systems refuse the NT ACL extended attributes.
  sudo samba-tool domain provision --targetdir="$DIR" --realm=NMAPS.TEST --domain=NMAPS \
    --server-role=dc --dns-backend=NONE --use-rfc2307 --adminpass="$ADMIN_PASSWORD" \
    --host-name=dc1 --option="interfaces=lo" --option="bind interfaces only=yes" \
    --option="vfs objects=dfs_samba4 acl_xattr xattr_tdb" >/dev/null
}

accounts() {
  local s=(-s "$CONF")
  sudo samba-tool user create svc-maps 'Svc-Pass-2026!x' --given-name=Service --surname=Maps "${s[@]}"
  sudo samba-tool user create jdoe 'Jane-Pass-2026!x' --given-name=Jane --surname=Doe "${s[@]}"
  sudo samba-tool user create bob 'Bob-Pass-2026!x' --given-name=Bob --surname=Builder "${s[@]}"
  sudo samba-tool group add noc-duty "${s[@]}"
  sudo samba-tool group addmembers noc-duty jdoe "${s[@]}"
}

wait_for_ldaps() {
  for _ in $(seq 60); do
    if (exec 3<>/dev/tcp/127.0.0.1/636) 2>/dev/null; then return 0; fi
    sleep 1
  done
  echo "LDAPS on 127.0.0.1:636 didn't come up" >&2
  return 1
}

case "${1:-}" in
  start)
    fresh=no
    if [ ! -f "$CONF" ]; then provision; fresh=yes; fi
    # The certificate is for DC1.nmaps.test: its name, so the check is a real one.
    grep -q "dc1.nmaps.test" /etc/hosts || echo "127.0.0.1 dc1.nmaps.test dc1" | sudo tee -a /etc/hosts >/dev/null
    sudo samba -D -M single -s "$CONF"
    wait_for_ldaps
    if [ "$fresh" = yes ]; then accounts; fi
    sudo cp "$DIR/private/tls/ca.pem" "$DIR/ca.pem"
    sudo chmod 644 "$DIR/ca.pem"
    echo "AD up: ldaps://dc1.nmaps.test, CA $DIR/ca.pem"
    ;;
  stop)
    sudo pkill -x samba || true
    ;;
  *)
    echo "usage: $0 start|stop" >&2
    exit 2
    ;;
esac
