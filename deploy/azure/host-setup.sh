#!/usr/bin/env bash
# Run once as root on the fresh Ubuntu 24.04 VM. Does not start the application.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || { echo 'Expected Ubuntu 24.04.' >&2; exit 1; }
[[ $(dpkg --print-architecture) == amd64 ]] || { echo 'Expected x64.' >&2; exit 1; }
apt-get update
apt-get install -y ca-certificates curl python3
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<SOURCES
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
SOURCES
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
install -d -m 0755 /opt/scorecopilot
install -d -m 0700 /etc/scorecopilot /srv/copilot/backups
install -d -o 10001 -g 10001 -m 0700 /srv/copilot/state
install -d -m 0700 /srv/copilot/caddy-data /srv/copilot/caddy-config
if [[ ! -e /etc/scorecopilot/api.env ]]; then
    umask 077
    python3 - <<'PY'
from pathlib import Path
import secrets
Path('/etc/scorecopilot/api.env').write_text('COPILOT_API_TOKEN='+secrets.token_hex(32)+'\n')
PY
fi
chmod 0600 /etc/scorecopilot/api.env
# Do not grant Docker-group access: it is equivalent to root.
docker compose version
findmnt -T /srv/copilot/state -o TARGET,SOURCE,FSTYPE
printf '%s\n' 'Host prepared. The application has not started. Runtime token was not printed.'
