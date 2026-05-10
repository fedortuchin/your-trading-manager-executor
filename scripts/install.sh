#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/ytm-executor"
IMAGE="ghcr.io/fedortuchin/your-trading-manager-executor:latest"
SERVER_URL=""
ENROLLMENT_TOKEN=""
BROKER_PROVIDER=""
START_SERVICE="true"

usage() {
  cat >&2 <<'EOF'
Usage:
  install.sh --server <url> --enrollment-token <token> [options]

Options:
  --broker-provider <tbank|binance>  Prompt locally for broker credentials after enrollment.
  --image <image>                    Default: ghcr.io/fedortuchin/your-trading-manager-executor:latest.
  --install-dir <path>               Default: /opt/ytm-executor.
  --no-start                         Install and enroll, but do not start the compose service.
  -h, --help                         Show this help.

Example:
  curl -fsSL https://raw.githubusercontent.com/fedortuchin/your-trading-manager-executor/main/scripts/install.sh | sudo bash -s -- \
    --server https://trademate.pro \
    --enrollment-token ytm_enroll_xxx
EOF
}

log() {
  printf '[ytm-executor] %s\n' "$*"
}

fail() {
  printf '[ytm-executor] ERROR: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server|--server-url)
      SERVER_URL="${2:-}"
      shift 2
      ;;
    --enrollment-token)
      ENROLLMENT_TOKEN="${2:-}"
      shift 2
      ;;
    --broker-provider)
      BROKER_PROVIDER="${2:-}"
      shift 2
      ;;
    --image)
      IMAGE="${2:-}"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="${2:-}"
      shift 2
      ;;
    --no-start)
      START_SERVICE="false"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      fail "unknown argument: $1"
      ;;
  esac
done

[[ "$(id -u)" == "0" ]] || fail "run through sudo/root"
[[ -n "$SERVER_URL" ]] || fail "--server is required"
[[ -n "$ENROLLMENT_TOKEN" ]] || fail "--enrollment-token is required"
[[ -n "$IMAGE" ]] || fail "--image must not be empty"
case "$BROKER_PROVIDER" in
  ""|"tbank"|"binance") ;;
  *) fail "--broker-provider must be tbank or binance" ;;
esac

install_prerequisites() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates curl
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y ca-certificates curl
  elif command -v yum >/dev/null 2>&1; then
    yum install -y ca-certificates curl
  else
    fail "supported package manager not found; install ca-certificates and curl first"
  fi
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y docker.io docker-compose-plugin || \
      apt-get install -y docker.io docker-compose-v2 || \
      apt-get install -y docker.io docker-compose
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y docker docker-compose-plugin
  elif command -v yum >/dev/null 2>&1; then
    yum install -y docker docker-compose-plugin
  else
    fail "supported package manager not found; install Docker Compose first"
  fi
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now docker
  fi
  command -v docker >/dev/null 2>&1 || fail "docker install failed"
}

detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
    return
  fi
  fail "Docker Compose is not available"
}

write_compose_file() {
  install -d -m 0755 "$INSTALL_DIR"
  cat >"${INSTALL_DIR}/docker-compose.yml" <<EOF
name: ytm-executor

services:
  ytm-executor:
    image: ${IMAGE}
    restart: unless-stopped
    command: run
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    volumes:
      - ytm_executor_state:/home/ytm-executor/.ytm-executor

volumes:
  ytm_executor_state:
EOF
}

compose() {
  "${COMPOSE[@]}" -f "${INSTALL_DIR}/docker-compose.yml" "$@"
}

enroll_executor() {
  compose run --rm -T ytm-executor enroll \
    --server-url "$SERVER_URL" \
    --enrollment-token "$ENROLLMENT_TOKEN"
}

configure_broker() {
  [[ -n "$BROKER_PROVIDER" ]] || return
  [[ -r /dev/tty ]] || fail "broker credential prompt requires an interactive terminal"
  log "prompting locally for ${BROKER_PROVIDER} credentials"
  compose run --rm ytm-executor broker add --provider "$BROKER_PROVIDER" < /dev/tty
}

log "installing prerequisites"
install_prerequisites
log "installing Docker Compose runtime"
install_docker
detect_compose
log "writing Docker Compose service in ${INSTALL_DIR}"
write_compose_file
log "pulling ${IMAGE}"
compose pull
log "enrolling executor with YTM"
enroll_executor
configure_broker
if [[ "$START_SERVICE" == "true" ]]; then
  log "starting executor container"
  compose up -d
else
  log "service start skipped"
fi
log "done"
