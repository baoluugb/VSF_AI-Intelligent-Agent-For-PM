import os

from dotenv import load_dotenv


load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# MCP server (Week 5 §5.1) — required value of the `X-API-Key` request header.
MCP_API_KEY = os.getenv("MCP_API_KEY", "")

DB_PATH = "data/vault.db"
CHROMA_PATH = "data/chroma/"

STALLED_DAYS = 3
DEADLINE_RISK_DAYS = 2
BLOCKER_OPEN_DAYS = 2
CONFLICT_WINDOW_H = 48
MAX_AGENT_ITERATIONS = 5

# A stalled task with no `needs-review` label that has been idle longer than this
# is treated as "chronic backlog": still reported, but de-prioritised (low
# severity) so it doesn't crowd out the items a PM must act on today.
CHRONIC_STALLED_DAYS = 30

# Report rendering ----------------------------------------------------------
# Language of the generated report + concern explanations: "vi" or "en".
REPORT_LANG = os.getenv("REPORT_LANG", "vi")
# Optional Jira base URL (e.g. https://acme.atlassian.net). When set, Jira issue
# citations like [FLINK-40] become clickable links. Empty → links disabled.
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "")

CHUNK_SIZE_CONFLUENCE = 600
CHUNK_OVERLAP_CONFLUENCE = 80
CHUNK_SIZE_MEETING = 300
CHUNK_OVERLAP_MEETING = 40


def validate_config() -> None:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is missing or empty.")
