#!/usr/bin/env bash
# Start a private native Secret Service session for packaged Linux verification.
set -euo pipefail

readonly usage_error=64
readonly unavailable_error=69
readonly startup_error=70

fail() {
  local message=$1
  local status=$2
  printf 'Linux keyring verifier: %s\n' "$message" >&2
  exit "$status"
}

stop_keyring() {
  if [[ -n "${keyring_pid:-}" ]]; then
    kill "$keyring_pid" 2>/dev/null || true
    wait "$keyring_pid" 2>/dev/null || true
  fi
}

run_inside_session() {
  [[ $# -ge 2 ]] || fail "missing isolated root or command" "$usage_error"
  local keyring_root=$1
  shift
  local keyring_home="$keyring_root/home"
  local keyring_runtime="$keyring_root/runtime"
  local keyring_control="$keyring_root/control"
  local unlock_file="$keyring_root/unlock"
  local daemon_log="$keyring_root/daemon.log"

  install -d -m 700 \
    "$keyring_home" \
    "$keyring_home/.cache" \
    "$keyring_home/.config" \
    "$keyring_home/.local/share" \
    "$keyring_runtime" \
    "$keyring_control"
  printf '\n' > "$unlock_file"
  chmod 600 "$unlock_file"

  HOME="$keyring_home" \
    XDG_CACHE_HOME="$keyring_home/.cache" \
    XDG_CONFIG_HOME="$keyring_home/.config" \
    XDG_DATA_HOME="$keyring_home/.local/share" \
    XDG_RUNTIME_DIR="$keyring_runtime" \
    gnome-keyring-daemon \
      --foreground \
      --unlock \
      --components=secrets \
      --control-directory="$keyring_control" \
      < "$unlock_file" > "$daemon_log" 2>&1 &
  keyring_pid=$!
  trap stop_keyring EXIT

  local ready=false
  local attempt
  for ((attempt = 0; attempt < 100; attempt += 1)); do
    if ! kill -0 "$keyring_pid" 2>/dev/null; then
      fail "native Secret Service exited during startup" "$startup_error"
    fi
    if dbus-send \
      --session \
      --dest=org.freedesktop.DBus \
      --type=method_call \
      --print-reply \
      /org/freedesktop/DBus \
      org.freedesktop.DBus.NameHasOwner \
      string:org.freedesktop.secrets 2>/dev/null | grep -Fq 'boolean true'; then
      ready=true
      break
    fi
    sleep 0.1
  done
  [[ "$ready" == true ]] || fail "native Secret Service did not become ready" "$startup_error"

  set +e
  "$@"
  local command_status=$?
  set -e
  exit "$command_status"
}

if [[ "${1:-}" == "--inside-session" ]]; then
  shift
  run_inside_session "$@"
fi

[[ "$(uname -s)" == Linux ]] || fail "this launcher supports Linux only" "$unavailable_error"
[[ $# -ge 1 ]] || fail "a command is required" "$usage_error"
for required_command in dbus-run-session dbus-send gnome-keyring-daemon; do
  command -v "$required_command" >/dev/null 2>&1 || \
    fail "required native command is unavailable: $required_command" "$unavailable_error"
done

script_directory="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
script_path="$script_directory/$(basename -- "$0")"
temporary_parent=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
keyring_root="$(mktemp -d "$temporary_parent/ancestryllm-keyring.XXXXXX")"
chmod 700 "$keyring_root"
cleanup_root() {
  rm -rf -- "$keyring_root"
}
trap cleanup_root EXIT

set +e
dbus-run-session -- "$script_path" --inside-session "$keyring_root" "$@"
session_status=$?
set -e
exit "$session_status"
