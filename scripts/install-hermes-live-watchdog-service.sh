#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
template="$repo_root/systemd/hermes-live-watchdog.service.in"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
unit_path="$unit_dir/hermes-live-watchdog.service"
hermes_home="${HERMES_HOME:-$HOME/.hermes}"

python_bin="$repo_root/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  python_bin="$repo_root/venv/bin/python"
fi
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3)"
fi

for value in "$repo_root" "$python_bin" "$hermes_home"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* || "$value" == *'|'* ]]; then
    echo "Service paths must not contain newlines or |" >&2
    exit 2
  fi
done

escape_sed() {
  printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

mkdir -p "$unit_dir"
sed \
  -e "s|@REPO_ROOT@|$(escape_sed "$repo_root")|g" \
  -e "s|@PYTHON@|$(escape_sed "$python_bin")|g" \
  -e "s|@HERMES_HOME@|$(escape_sed "$hermes_home")|g" \
  "$template" >"$unit_path"

systemctl --user daemon-reload
systemctl --user enable --now hermes-live-watchdog.service
echo "Installed and started hermes-live-watchdog.service"
