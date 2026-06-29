#!/usr/bin/env bash
# =============================================================================
# scripts/daily_run.sh — daily incremental run (accumulates snapshot history)
# =============================================================================
#
# Usage:
#   ./scripts/daily_run.sh [DATE]
#
#   DATE   ISO date (YYYY-MM-DD) used as the "today" reference. Defaults to the
#          real current date, so a daily cron builds up genuine day-over-day
#          history.
#
# Unlike run_agent.sh (which passes --reset for a clean demo baseline), this
# wrapper does NOT reset the store: snapshots accumulate across runs, so the
# report's "Recent Changes" section reflects real day-over-day movement and the
# day-over-day diff is meaningful. Wire it to cron/systemd for a daily digest:
#
#   0 7 * * *  cd /path/to/repo && ./scripts/daily_run.sh >> logs/daily.log 2>&1
#
# When SLACK_WEBHOOK_URL is set (see .env), each run also posts a digest to Slack.
#
# Requires: Python 3.10+, deps installed, OPENAI_API_KEY in .env
# =============================================================================
set -euo pipefail

DATE="${1:-$(date +%F)}"

# Resolve repo root from this script's location so cron can call it by any path.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "${SCRIPT_DIR}")"
cd "${REPO_ROOT}"

echo "=== Daily run (incremental, accumulating history) — date ${DATE} ==="
python src/run_agent.py --date "${DATE}"
