#!/bin/bash
# Bring up a single-node ClickHouse on this VM.
#
# The image tag is pinned. Never :latest - an unattended pull of a new major
# version overnight is exactly how a working deployment breaks the morning of a
# deadline.
#
# This is the instance startup-script, so it runs again on every boot and must
# stay idempotent.
set -euo pipefail

CH_IMAGE="clickhouse/clickhouse-server:25.8.33.6"

log() { echo "[greenlight-startup] $*"; }

meta() {
  curl -sf -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"
}

if ! command -v docker >/dev/null 2>&1; then
  log "installing docker"
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io
fi

mkdir -p /var/lib/clickhouse /etc/clickhouse-config.d /etc/clickhouse-users.d

# Server-wide settings belong in config.d.
# ClickHouse will happily use the whole box on a bad query; cap it so a runaway
# aggregate cannot take the instance down with it.
cat > /etc/clickhouse-config.d/greenlight.xml <<'XML'
<clickhouse>
    <max_server_memory_usage_to_ram_ratio>0.7</max_server_memory_usage_to_ram_ratio>
    <mark_cache_size>268435456</mark_cache_size>
    <listen_host>0.0.0.0</listen_host>
</clickhouse>
XML

# Per-query ceilings are PROFILES, and profiles live in users.d - not config.d.
# This is the server-side half of the SQL guard in app/store/queries.py, and it
# also catches anything an agent manages to smuggle past that guard.
cat > /etc/clickhouse-users.d/greenlight-limits.xml <<'XML'
<clickhouse>
    <profiles>
        <default>
            <max_execution_time>15</max_execution_time>
            <max_memory_usage>1500000000</max_memory_usage>
            <max_result_rows>5000</max_result_rows>
            <result_overflow_mode>break</result_overflow_mode>
            <max_rows_to_read>50000000</max_rows_to_read>
            <read_overflow_mode>throw</read_overflow_mode>
        </default>
    </profiles>
</clickhouse>
XML

# From here the password is in play. Keep it out of the serial console, which is
# readable by anyone with compute.instances.getSerialPortOutput.
set +x
CH_PASSWORD="$(meta ch-password | tr -d '\r\n')"
if [ -z "$CH_PASSWORD" ]; then
  log "FATAL: ch-password metadata is empty"
  exit 1
fi

if [ "$(docker ps -aq -f name=^clickhouse$)" ]; then
  log "removing existing container"
  docker rm -f clickhouse >/dev/null
fi

log "starting $CH_IMAGE"
docker run -d --name clickhouse --restart unless-stopped \
  -p 8123:8123 -p 9000:9000 \
  -e CLICKHOUSE_USER=greenlight \
  -e CLICKHOUSE_PASSWORD="$CH_PASSWORD" \
  -e CLICKHOUSE_DB=greenlight \
  -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 \
  -v /var/lib/clickhouse:/var/lib/clickhouse \
  -v /etc/clickhouse-config.d:/etc/clickhouse-server/config.d \
  -v /etc/clickhouse-users.d:/etc/clickhouse-server/users.d \
  --ulimit nofile=262144:262144 \
  "$CH_IMAGE" >/dev/null
unset CH_PASSWORD

log "clickhouse started from $CH_IMAGE"
