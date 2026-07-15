#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
template="$repo_root/systemd/hermes-full-backup@.service.in"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
hermes_home="${HERMES_HOME:-$HOME/.hermes}"
backup_root="${HERMES_BACKUP_ROOT:-$HOME/.hermes-backups}"

python_bin="$repo_root/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  python_bin="$repo_root/venv/bin/python"
fi
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3)"
fi

for value in "$repo_root" "$python_bin" "$hermes_home" "$backup_root"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* || "$value" == *'|'* ]]; then
    echo "Backup service paths must not contain newlines or |" >&2
    exit 2
  fi
done

escape_sed() {
  printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

mkdir -p "$unit_dir" "$backup_root"
sed \
  -e "s|@REPO_ROOT@|$(escape_sed "$repo_root")|g" \
  -e "s|@PYTHON@|$(escape_sed "$python_bin")|g" \
  -e "s|@HERMES_HOME@|$(escape_sed "$hermes_home")|g" \
  -e "s|@BACKUP_ROOT@|$(escape_sed "$backup_root")|g" \
  "$template" >"$unit_dir/hermes-full-backup@.service"
cp "$repo_root/systemd/hermes-full-backup-daily.timer" "$unit_dir/"
cp "$repo_root/systemd/hermes-full-backup-weekly.timer" "$unit_dir/"

systemctl --user daemon-reload
systemctl --user enable --now hermes-full-backup-daily.timer
systemctl --user enable --now hermes-full-backup-weekly.timer
echo "Installed Hermes full-backup timers with output root: $backup_root"
