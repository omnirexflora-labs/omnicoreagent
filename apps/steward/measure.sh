#!/bin/sh
# One line an hour about the steward deployment, for P7 (a week unattended):
#   0 * * * * /opt/steward/src/apps/steward/measure.sh >> /opt/steward/logs/p7.csv 2>/dev/null
# Prints the header when the file does not exist yet.
set -a; . /opt/steward/.env; set +a
CSV=/opt/steward/logs/p7.csv
API=http://127.0.0.1:8800
auth="Authorization: Bearer $OMNICOREAGENT_SERVE_AUTH_TOKEN"

[ -s "$CSV" ] || echo "time,serve_mem_mib,serve_pids,serve_fds,workspace_mib,telemetry_mib,postgres_mib,redis_keys,redis_mib,runs_total,runs_running,runs_failed,day_spent_usd"

mem=$(docker stats --no-stream --format "{{.MemUsage}}" steward-serve | awk '{print $1}' | sed 's/MiB//; s/GiB/*1024/' | bc 2>/dev/null)
pids=$(docker stats --no-stream --format "{{.PIDs}}" steward-serve)
fds=$(docker exec steward-serve sh -c 'ls /proc/1/fd 2>/dev/null | wc -l')
ws=$(docker exec steward-serve sh -c 'du -sm /app/workspace/files 2>/dev/null | cut -f1')
tel=$(docker exec steward-serve sh -c 'du -sm /app/workspace/telemetry 2>/dev/null | cut -f1')
pg=$(docker exec steward-postgres psql -U steward -d steward -tAc "select pg_database_size('steward')/1048576" 2>/dev/null)
rkeys=$(docker exec steward-redis redis-cli dbsize | awk '{print $NF}')
rmem=$(docker exec steward-redis redis-cli info memory | awk -F: '/^used_memory:/{printf "%.1f", $2/1048576}')
runs=$(curl -s -H "$auth" "$API/background/runs" | python3 -c '
import json,sys; d=json.load(sys.stdin); r=d.get("runs",d)
print(len(r), sum(1 for x in r if x["status"] in ("running","claimed","queued")), sum(1 for x in r if x["status"] in ("failed","timeout")), (r[0]["run_id"] if r else ""))' 2>/dev/null)
set -- $runs
total=${1:-0}; running=${2:-0}; failed=${3:-0}; latest=${4:-}
day=""
[ -n "$latest" ] && day=$(curl -s -H "$auth" "$API/runs/$latest/budget" | python3 -c '
import json,sys; d=json.load(sys.stdin)
print(next((round(b["spent"],4) for b in d.get("budgets",[]) if b["scope"]=="application" and b["meter"]=="model_cost_usd"), ""))' 2>/dev/null)
echo "$(date -u +%FT%TZ),${mem:-},${pids:-},${fds:-},${ws:-},${tel:-},${pg:-},${rkeys:-},${rmem:-},$total,$running,$failed,$day"
