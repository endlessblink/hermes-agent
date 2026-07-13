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
  printf '%s' "$value"
}

escaped_python="$(escape_unit_value "$python_bin")"
escaped_repo="$(escape_unit_value "$repo_root")"
escaped_profile="$(escape_unit_value "$profile_home")"

cat >"$unit_dir/hermes-personal-assistant-monitor.service" <<EOF
[Unit]
Description=Hermes personal assistant FlowState monitor

[Service]
Type=oneshot
TimeoutStartSec=45s
WorkingDirectory=$escaped_repo
ExecStart="$escaped_python" -m agent.personal_assistant_monitor --profile-home "$escaped_profile"
EOF

cat >"$unit_dir/hermes-personal-assistant-monitor.timer" <<'EOF'
[Unit]
Description=Check FlowState for material personal-assistant changes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
echo "Installed but not enabled: hermes-personal-assistant-monitor.timer"
