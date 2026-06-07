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

CHUNK_SIZE_CONFLUENCE = 600
CHUNK_OVERLAP_CONFLUENCE = 80
CHUNK_SIZE_MEETING = 300
CHUNK_OVERLAP_MEETING = 40


def validate_config() -> None:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is missing or empty.")
