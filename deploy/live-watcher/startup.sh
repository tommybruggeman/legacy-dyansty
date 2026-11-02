#!/bin/bash
# Boot script for the free e2-micro that runs the game-day live stats watcher (scripts/live_watcher.py).
# Runs on every boot: installs what's missing, pulls the latest main from the public repo, reads the Supabase
# settings from Google Secret Manager (secrets legacy-supabase-url and legacy-supabase-service-key), and starts
# the watcher as a systemd service that restarts itself if it ever stops. It also pulls the latest code daily.
set -euo pipefail

APP=/opt/legacy
REPO=https://github.com/tommybruggeman/legacy-dyansty.git

if ! command -v git >/dev/null || ! python3 -m venv --help >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y git python3-venv
fi
# 1 GB of memory: a small swap file keeps pandas (used once a day for the player id list) from running out.
if [ ! -f /swapfile ]; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
if [ ! -d "$APP/.git" ]; then
  git clone --depth 1 "$REPO" "$APP"
fi
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --quiet --upgrade supabase requests pandas

secret() { gcloud secrets versions access latest --secret="$1" --quiet; }
umask 077
cat > /etc/legacy-watcher.env <<ENV
SUPABASE_URL=$(secret legacy-supabase-url)
SUPABASE_SERVICE_ROLE_KEY=$(secret legacy-supabase-service-key)
ROLE=main
INTERVAL=5
ENV

cat > /etc/systemd/system/legacy-watcher.service <<UNIT
[Unit]
Description=Legacy Dynasty live stats watcher
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=/etc/legacy-watcher.env
WorkingDirectory=$APP
ExecStartPre=-/usr/bin/git -C $APP pull --ff-only --quiet
ExecStart=$APP/.venv/bin/python scripts/live_watcher.py
Restart=always
RestartSec=30
MemoryMax=700M

[Install]
WantedBy=multi-user.target
UNIT

# Restart once a day at 9:00 UTC (no games then) so it picks up new code from main.
cat > /etc/systemd/system/legacy-watcher-update.service <<UNIT
[Unit]
Description=Restart the live stats watcher on the latest code

[Service]
Type=oneshot
ExecStart=/bin/systemctl restart legacy-watcher.service
UNIT
cat > /etc/systemd/system/legacy-watcher-update.timer <<UNIT
[Unit]
Description=Daily watcher code update

[Timer]
OnCalendar=*-*-* 09:00:00 UTC

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now legacy-watcher.service legacy-watcher-update.timer
systemctl restart legacy-watcher.service
