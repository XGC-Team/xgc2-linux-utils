#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Configure bounded system and Docker logging on an Ubuntu/Debian server.

Usage:
  sudo scripts/configure-log-limits.sh [options]

Options:
  --docker-max-size SIZE      Docker json-file max size per log file. Default: 50m
  --docker-max-file COUNT     Docker json-file rotated file count. Default: 3
  --journal-max-use SIZE      journald total disk cap. Default: 300M
  --journal-keep-free SIZE    journald free-space target. Default: 1G
  --journal-file-size SIZE    journald max file size. Default: 50M
  --journal-retention VALUE   journald max retention. Default: 7day
  --syslog-size SIZE          /var/log/syslog max size before rotation. Default: 100M
  --syslog-rotate COUNT       /var/log/syslog rotated file count. Default: 3
  --auxlog-size SIZE          other rsyslog file max size before rotation. Default: 50M
  --auxlog-rotate COUNT       other rsyslog rotated file count. Default: 2
  --truncate-docker-logs      Truncate existing Docker *-json.log files now.
  --truncate-system-logs      Truncate current /var/log/syslog and common rsyslog files now.
  --no-restart                Write config only; do not restart services.
  -h, --help                  Show this help.

Notes:
  - Existing config files are backed up under /var/backups/log-limits/.
  - Docker log limits apply to newly created containers. Recreate old containers
    to make their LogConfig inherit the new defaults.
EOF
}

original_args=("$@")

docker_max_size="50m"
docker_max_file="3"
journal_max_use="300M"
journal_keep_free="1G"
journal_file_size="50M"
journal_retention="7day"
syslog_size="100M"
syslog_rotate="3"
auxlog_size="50M"
auxlog_rotate="2"
truncate_docker_logs=false
truncate_system_logs=false
restart_services=true

while [ "$#" -gt 0 ]; do
  case "$1" in
    --docker-max-size)
      docker_max_size="${2:?missing value for $1}"
      shift 2
      ;;
    --docker-max-file)
      docker_max_file="${2:?missing value for $1}"
      shift 2
      ;;
    --journal-max-use)
      journal_max_use="${2:?missing value for $1}"
      shift 2
      ;;
    --journal-keep-free)
      journal_keep_free="${2:?missing value for $1}"
      shift 2
      ;;
    --journal-file-size)
      journal_file_size="${2:?missing value for $1}"
      shift 2
      ;;
    --journal-retention)
      journal_retention="${2:?missing value for $1}"
      shift 2
      ;;
    --syslog-size)
      syslog_size="${2:?missing value for $1}"
      shift 2
      ;;
    --syslog-rotate)
      syslog_rotate="${2:?missing value for $1}"
      shift 2
      ;;
    --auxlog-size)
      auxlog_size="${2:?missing value for $1}"
      shift 2
      ;;
    --auxlog-rotate)
      auxlog_rotate="${2:?missing value for $1}"
      shift 2
      ;;
    --truncate-docker-logs)
      truncate_docker_logs=true
      shift
      ;;
    --truncate-system-logs)
      truncate_system_logs=true
      shift
      ;;
    --no-restart)
      restart_services=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  exec sudo "$0" "${original_args[@]}"
fi

backup_dir="/var/backups/log-limits/$(date +%Y%m%d%H%M%S)"
mkdir -p "${backup_dir}"

backup_file() {
  local path="$1"
  if [ -e "${path}" ]; then
    mkdir -p "${backup_dir}$(dirname "${path}")"
    cp -a "${path}" "${backup_dir}${path}"
  fi
}

service_is_active() {
  systemctl is-active --quiet "$1" 2>/dev/null
}

configure_docker() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required to merge /etc/docker/daemon.json" >&2
    exit 1
  fi

  mkdir -p /etc/docker
  if [ ! -f /etc/docker/daemon.json ]; then
    printf '{}\n' > /etc/docker/daemon.json
  fi

  backup_file /etc/docker/daemon.json

  DOCKER_MAX_SIZE="${docker_max_size}" DOCKER_MAX_FILE="${docker_max_file}" \
    python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path("/etc/docker/daemon.json")
try:
    data = json.loads(path.read_text() or "{}")
except json.JSONDecodeError as exc:
    raise SystemExit(f"/etc/docker/daemon.json is not valid JSON: {exc}")

data["log-driver"] = "json-file"
log_opts = dict(data.get("log-opts") or {})
log_opts["max-size"] = os.environ["DOCKER_MAX_SIZE"]
log_opts["max-file"] = os.environ["DOCKER_MAX_FILE"]
data["log-opts"] = log_opts

tmp = path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
tmp.replace(path)
PY

  if [ "${restart_services}" = true ] && service_is_active docker.service; then
    systemctl restart docker.service
  fi
}

configure_journald() {
  mkdir -p /etc/systemd/journald.conf.d
  backup_file /etc/systemd/journald.conf.d/99-log-limits.conf
  cat > /etc/systemd/journald.conf.d/99-log-limits.conf <<EOF
[Journal]
SystemMaxUse=${journal_max_use}
SystemKeepFree=${journal_keep_free}
SystemMaxFileSize=${journal_file_size}
MaxRetentionSec=${journal_retention}
EOF

  if [ "${restart_services}" = true ] && service_is_active systemd-journald.service; then
    systemctl restart systemd-journald.service
  fi

  if command -v journalctl >/dev/null 2>&1; then
    journalctl --vacuum-size="${journal_max_use}" >/dev/null || true
  fi
}

configure_rsyslog_logrotate() {
  backup_file /etc/logrotate.d/rsyslog
  cat > /etc/logrotate.d/rsyslog <<EOF
/var/log/syslog
{
    su root adm
    rotate ${syslog_rotate}
    daily
    maxsize ${syslog_size}
    missingok
    notifempty
    compress
    delaycompress
    postrotate
        if [ -x /usr/lib/rsyslog/rsyslog-rotate ]; then
            /usr/lib/rsyslog/rsyslog-rotate
        else
            systemctl kill -s HUP rsyslog.service >/dev/null 2>&1 || true
        fi
    endscript
}

/var/log/mail.info
/var/log/mail.warn
/var/log/mail.err
/var/log/mail.log
/var/log/daemon.log
/var/log/kern.log
/var/log/auth.log
/var/log/user.log
/var/log/lpr.log
/var/log/cron.log
/var/log/debug
/var/log/messages
{
    su root adm
    rotate ${auxlog_rotate}
    weekly
    maxsize ${auxlog_size}
    missingok
    notifempty
    compress
    delaycompress
    sharedscripts
    postrotate
        if [ -x /usr/lib/rsyslog/rsyslog-rotate ]; then
            /usr/lib/rsyslog/rsyslog-rotate
        else
            systemctl kill -s HUP rsyslog.service >/dev/null 2>&1 || true
        fi
    endscript
}
EOF

  if command -v logrotate >/dev/null 2>&1; then
    logrotate -d /etc/logrotate.d/rsyslog >/dev/null
  fi
}

truncate_existing_logs() {
  if [ "${truncate_docker_logs}" = true ] && [ -d /var/lib/docker/containers ]; then
    find /var/lib/docker/containers -name '*-json.log' -type f -exec truncate -s 0 {} +
  fi

  if [ "${truncate_system_logs}" = true ]; then
    for log in \
      /var/log/syslog \
      /var/log/mail.info \
      /var/log/mail.warn \
      /var/log/mail.err \
      /var/log/mail.log \
      /var/log/daemon.log \
      /var/log/kern.log \
      /var/log/auth.log \
      /var/log/user.log \
      /var/log/lpr.log \
      /var/log/cron.log \
      /var/log/debug \
      /var/log/messages; do
      [ -f "${log}" ] && truncate -s 0 "${log}"
    done
  fi
}

print_summary() {
  cat <<EOF
Log limits configured.

Backups:
  ${backup_dir}

Docker:
  max-size=${docker_max_size}
  max-file=${docker_max_file}
  note: recreate existing containers for this default to appear in docker inspect LogConfig.

journald:
  SystemMaxUse=${journal_max_use}
  SystemKeepFree=${journal_keep_free}
  SystemMaxFileSize=${journal_file_size}
  MaxRetentionSec=${journal_retention}

rsyslog/logrotate:
  /var/log/syslog size=${syslog_size}, rotate=${syslog_rotate}
  auxiliary logs size=${auxlog_size}, rotate=${auxlog_rotate}

Current disk:
EOF
  df -h /
}

configure_docker
configure_journald
configure_rsyslog_logrotate
truncate_existing_logs
print_summary
