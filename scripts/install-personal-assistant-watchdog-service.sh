#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --profile-home PATH"
}

profile_home=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile-home)
      profile_home="${2:-}"
      shift 2
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$profile_home" ]]; then
  usage >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$repo_root/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3)"
fi
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"

for value in "$python_bin" "$repo_root" "$profile_home"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "Paths must not contain newlines" >&2
    exit 2
  fi
done

escape_unit_value() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//%/%%}"
  printf '%s' "$value"
}

escaped_python="$(escape_unit_value "$python_bin")"
escaped_repo="$(escape_unit_value "$repo_root")"
escaped_profile="$(escape_unit_value "$profile_home")"

cat >"$unit_dir/hermes-personal-assistant-watchdog.service" <<EOF
[Unit]
Description=Hermes personal assistant monitor watchdog
After=hermes-personal-assistant-monitor.service

[Service]
Type=oneshot
TimeoutStartSec=15s
Environment="HERMES_HOME=$escaped_profile"
WorkingDirectory=$escaped_repo
ExecStart="$escaped_python" "$escaped_repo/scripts/hermes_live_watchdog.py" --profile-home "$escaped_profile" --notify
EOF

cat >"$unit_dir/hermes-personal-assistant-watchdog.timer" <<'EOF'
[Unit]
Description=Check Hermes personal assistant monitor reliability

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
AccuracySec=15s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
echo "Installed but not enabled: hermes-personal-assistant-watchdog.timer"
