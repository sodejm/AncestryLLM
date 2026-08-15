#!/usr/bin/env bash
# Start a disposable native Secret Service session for packaged Linux verification.
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

session_bus_responds() {
  dbus-send \
    --session \
    --dest=org.freedesktop.DBus \
    --type=method_call \
    --print-reply \
    /org/freedesktop/DBus \
    org.freedesktop.DBus.ListNames >/dev/null 2>&1
}

query_secret_service_owner() {
  dbus-send \
    --session \
    --dest=org.freedesktop.DBus \
    --type=method_call \
    --print-reply \
    /org/freedesktop/DBus \
    org.freedesktop.DBus.NameHasOwner \
    string:org.freedesktop.secrets
}

require_secret_service_unowned() {
  local owner_reply
  owner_reply="$(query_secret_service_owner 2>/dev/null)" || \
    fail "D-Bus endpoint could not report Secret Service ownership" "$startup_error"
  if grep -Fq 'boolean true' <<< "$owner_reply"; then
    fail "Secret Service endpoint is already occupied" "$startup_error"
  fi
  grep -Fq 'boolean false' <<< "$owner_reply" || \
    fail "D-Bus endpoint returned an invalid Secret Service ownership reply" \
      "$startup_error"
}

stop_keyring() {
  if [[ -n "${keyring_pid:-}" ]]; then
    kill "$keyring_pid" 2>/dev/null || true
    wait "$keyring_pid" 2>/dev/null || true
  fi
}

run_inside_session() {
  [[ $# -ge 3 ]] || fail "missing isolated root, runtime directory, or command" "$usage_error"
  local keyring_root=$1
  local session_runtime=$2
  shift 2
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

  export HOME="$keyring_home"
  export XDG_CACHE_HOME="$keyring_home/.cache"
  export XDG_CONFIG_HOME="$keyring_home/.config"
  export XDG_DATA_HOME="$keyring_home/.local/share"
  export XDG_RUNTIME_DIR="$session_runtime"
  require_secret_service_unowned
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
    if query_secret_service_owner 2>/dev/null | grep -Fq 'boolean true'; then
      ready=true
      break
    fi
    sleep 0.1
  done
  [[ "$ready" == true ]] || fail "native Secret Service did not become ready" "$startup_error"

  local probe_value="ancestryllm-native-keyring-probe"
  printf '%s' "$probe_value" | secret-tool store \
    --label="AncestryLLM verifier probe" \
    service ancestryllm-verifier key bootstrap || \
    fail "native Secret Service could not store a verifier probe" "$startup_error"
  local observed_probe
  observed_probe="$(secret-tool lookup service ancestryllm-verifier key bootstrap)" || \
    fail "native Secret Service could not read the verifier probe" "$startup_error"
  [[ "$observed_probe" == "$probe_value" ]] || \
    fail "native Secret Service returned the wrong verifier probe" "$startup_error"
  secret-tool clear service ancestryllm-verifier key bootstrap || \
    fail "native Secret Service could not delete the verifier probe" "$startup_error"
  export ANCESTRYLLM_NATIVE_KEYRING_ROOT="$keyring_root"
  set +e
  "$@"
  local command_status=$?
  set -e
  exit "$command_status"
}

production_runtime_bus=false
if [[ "${1:-}" == "--production-runtime-bus" ]]; then
  production_runtime_bus=true
  shift
fi

if [[ "${1:-}" == "--inside-session" ]]; then
  shift
  run_inside_session "$@"
fi

[[ "$(uname -s)" == Linux ]] || fail "this launcher supports Linux only" "$unavailable_error"
[[ $# -ge 1 ]] || fail "a command is required" "$usage_error"
for required_command in dbus-daemon dbus-send gnome-keyring-daemon id install secret-tool stat; do
  command -v "$required_command" >/dev/null 2>&1 || \
    fail "required native command is unavailable: $required_command" "$unavailable_error"
done

script_directory="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
script_path="$script_directory/$(basename -- "$0")"
temporary_parent=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
keyring_root="$(mktemp -d "$temporary_parent/ancestryllm-keyring.XXXXXX")"
chmod 700 "$keyring_root"
install -d -m 700 "$keyring_root/runtime"
bus_log="$keyring_root/bus.log"
dbus_pid=
production_runtime_directory=
production_runtime_created=false
production_socket_owned=false
production_socket_identity=
reuse_existing_production_bus=false
session_runtime_directory="$keyring_root/runtime"
session_socket="$session_runtime_directory/bus"
cleanup_root() {
  if [[ -n "${dbus_pid:-}" ]]; then
    kill "$dbus_pid" 2>/dev/null || true
    wait "$dbus_pid" 2>/dev/null || true
  fi
  if [[ "$production_runtime_bus" == true ]]; then
    if [[ "$production_socket_owned" == true && ! -L "$session_socket" && -S "$session_socket" ]]; then
      current_socket_identity="$(stat -c '%d:%i' -- "$session_socket" 2>/dev/null || true)"
      if [[ "$current_socket_identity" == "$production_socket_identity" ]]; then
        rm -f -- "$session_socket"
      fi
    fi
    if [[ "$production_runtime_created" == true ]]; then
      sudo rmdir -- "$production_runtime_directory" 2>/dev/null || true
    fi
  fi
  rm -rf -- "$keyring_root"
}
trap cleanup_root EXIT

if [[ "$production_runtime_bus" == true ]]; then
  user_id="$(id -u)"
  user_group_id="$(id -g)"
  production_runtime_directory="/run/user/$user_id"
  if [[ -e "$production_runtime_directory" || -L "$production_runtime_directory" ]]; then
    [[ -d "$production_runtime_directory" && ! -L "$production_runtime_directory" ]] || \
      fail "production runtime path is not a real directory" "$startup_error"
  else
    command -v sudo >/dev/null 2>&1 || \
      fail "sudo is required to create the production runtime directory" "$unavailable_error"
    production_runtime_created=true
    sudo install \
      -d \
      -o "$user_id" \
      -g "$user_group_id" \
      -m 700 \
      -- "$production_runtime_directory"
  fi
  runtime_metadata="$(stat -c '%u:%g:%a' -- "$production_runtime_directory")" || \
    fail "production runtime metadata is unavailable" "$startup_error"
  [[ "$runtime_metadata" == "$user_id:$user_group_id:700" ]] || \
    fail "production runtime directory must be owner-only and owned by the current user" \
      "$startup_error"
  session_runtime_directory="$production_runtime_directory"
  session_socket="$production_runtime_directory/bus"
fi

session_address="unix:path=$session_socket"
export DBUS_SESSION_BUS_ADDRESS="$session_address"

if [[ "$production_runtime_bus" == true ]]; then
  if [[ -e "$session_socket" || -L "$session_socket" ]]; then
    [[ ! -L "$session_socket" && -S "$session_socket" ]] || \
      fail "production D-Bus endpoint must be a current-user Unix socket" "$startup_error"
    production_socket_metadata="$(stat -c '%u:%g:%d:%i' -- "$session_socket")" || \
      fail "production D-Bus endpoint metadata is unavailable" "$startup_error"
    IFS=: read -r socket_user_id socket_group_id socket_device socket_inode \
      <<< "$production_socket_metadata"
    [[ "$socket_user_id" == "$user_id" && "$socket_group_id" == "$user_group_id" ]] || \
      fail "production D-Bus endpoint must be a current-user Unix socket" "$startup_error"
    production_socket_identity="$socket_device:$socket_inode"
    session_bus_responds || \
      fail "production D-Bus endpoint is not a working session bus" "$startup_error"
    require_secret_service_unowned
    [[ ! -L "$session_socket" && -S "$session_socket" ]] || \
      fail "production D-Bus endpoint changed during validation" "$startup_error"
    current_socket_metadata="$(stat -c '%u:%g:%d:%i' -- "$session_socket")" || \
      fail "production D-Bus endpoint changed during validation" "$startup_error"
    [[ "$current_socket_metadata" == "$production_socket_metadata" ]] || \
      fail "production D-Bus endpoint changed during validation" "$startup_error"
    reuse_existing_production_bus=true
  fi
fi

if [[ "$reuse_existing_production_bus" != true ]]; then
  dbus-daemon \
    --session \
    --nofork \
    --address="$session_address" \
    > "$bus_log" 2>&1 &
  dbus_pid=$!

  bus_ready=false
  for ((attempt = 0; attempt < 100; attempt += 1)); do
    if ! kill -0 "$dbus_pid" 2>/dev/null; then
      fail "private D-Bus session exited during startup" "$startup_error"
    fi
    if session_bus_responds; then
      bus_ready=true
      break
    fi
    sleep 0.1
  done
  [[ "$bus_ready" == true ]] || fail "private D-Bus session did not become ready" "$startup_error"
  if [[ "$production_runtime_bus" == true ]]; then
    [[ ! -L "$session_socket" && -S "$session_socket" ]] || \
      fail "production D-Bus endpoint is not a socket" "$startup_error"
    production_socket_metadata="$(stat -c '%u:%g:%d:%i' -- "$session_socket")" || \
      fail "production D-Bus endpoint metadata is unavailable" "$startup_error"
    IFS=: read -r socket_user_id socket_group_id socket_device socket_inode \
      <<< "$production_socket_metadata"
    [[ "$socket_user_id" == "$user_id" && "$socket_group_id" == "$user_group_id" ]] || \
      fail "production D-Bus endpoint must be owned by the current user and group" \
        "$startup_error"
    production_socket_identity="$socket_device:$socket_inode"
    production_socket_owned=true
  fi
fi

set +e
"$script_path" --inside-session "$keyring_root" "$session_runtime_directory" "$@"
session_status=$?
set -e
exit "$session_status"
