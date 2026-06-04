#!/usr/bin/env bash
# =============================================================================
# run_agent.sh — AI Project Intelligence Agent, one-command runner (Week 6)
# =============================================================================
#
# Usage:
#   ./run_agent.sh [DATE]
#
#   DATE   ISO date (YYYY-MM-DD) used as the "today" reference.
#          Defaults to 2025-05-30 (the sweet spot of the synthetic data).
#
# Rebuilds the dual store, then runs ingestion → Concern Engine → (grounded)
# Report Agent in a single process and writes:
#   output/report.md       — narrative daily report (cited)
#   output/concerns.json   — deterministic, severity-sorted risk list
#
# Requires: Python 3.9+, deps installed, OPENAI_API_KEY in .env
# =============================================================================
set -euo pipefail

DATE="${1:-2025-05-30}"

CYAN="\033[0;36m"; GREEN="\033[0;32m"; YELLOW="\033[1;33m"
GRAY="\033[0;37m"; MAGENTA="\033[0;35m"; RESET="\033[0m"
OK="✔"; WARN="⚠"
step() { echo -e "\n${CYAN}$*${RESET}"; }
ok()   { echo -e "  ${GREEN}${OK}  $*${RESET}"; }
info() { echo -e "  ${GRAY}→  $*${RESET}"; }

echo -e ""
echo -e "${MAGENTA}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${MAGENTA}║   AI Project Intelligence Agent  (run_agent.sh)  ║${RESET}"
echo -e "${MAGENTA}╚══════════════════════════════════════════════════╝${RESET}"
echo    "  Date : ${DATE}"

# ── step 1 — reset dual store ────────────────────────────────────────────────
step "[1/2] Resetting dual store (SQLite + ChromaDB)..."
[ -f "data/vault.db" ] && { rm -f data/vault.db; info "Removed data/vault.db"; }
[ -d "data/chroma" ]   && { rm -rf data/chroma/; info "Removed data/chroma/"; }
ok "Stores reset."

# ── step 2 — ingest → concerns → grounded report ─────────────────────────────
step "[2/2] Ingesting + generating concerns and grounded report..."
python src/run_agent.py --date "${DATE}"

# ── verification ─────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}║                 Run complete ✔                   ║${RESET}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${RESET}"

REPORT_SIZE=$(wc -c < output/report.md 2>/dev/null || echo 0)
CONCERN_COUNT=$(python -c "import json;print(len(json.load(open('output/concerns.json'))))" 2>/dev/null || echo "?")
echo "  output/report.md     — ${REPORT_SIZE} bytes"
echo "  output/concerns.json — ${CONCERN_COUNT} concern(s)"
echo ""
echo "  Quick verification:"

CITATIONS=$(grep -oE '\[[A-Z]+-[^]]+\]' output/report.md 2>/dev/null | wc -l | tr -d ' ' || echo 0)
if [ "${CITATIONS:-0}" -ge 5 ] 2>/dev/null; then
    echo -e "    Citations in report : ${GREEN}${CITATIONS} ${OK} (V2 met)${RESET}"
else
    echo -e "    Citations in report : ${YELLOW}${CITATIONS} ${WARN} (V2 needs >=5)${RESET}"
fi

TYPES=("stalled_task" "deadline_risk" "unresolved_blocker" "cross_source_conflict")
FOUND=0
for t in "${TYPES[@]}"; do
    grep -q "\"${t}\"" output/concerns.json 2>/dev/null && FOUND=$((FOUND + 1))
done
if [ "$FOUND" -eq 4 ]; then
    echo -e "    Concern types found : ${GREEN}all 4 ${OK} (V3 met)${RESET}"
else
    echo -e "    Concern types found : ${YELLOW}${FOUND}/4 ${WARN}${RESET}"
fi
echo ""
