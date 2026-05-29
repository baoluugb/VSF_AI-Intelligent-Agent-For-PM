# AI Agent Project

This repository contains ingestion connectors, storage helpers, and tests for a planned AI agent pipeline. It also includes synthetic Jira, Confluence, and meeting notes data under `data/` for local validation.

## Project Layout

```
VSF_Project/
├── AI_Project_Intelligence_Agent_Plan.md
├── README.md
├── config.py
├── data/
│   ├── confluence/
│   │   └── confluence_synthetic.json
│   ├── jira/
│   │   ├── jira_data_sources.json
│   │   ├── jira_field_information.json
│   │   ├── jira_issue_linktype_mapping.json
│   │   ├── jira_issuelinktype_information.json
│   │   ├── jira_issuetype_information.json
│   │   ├── jira_issuetype_thematic_analysis.json
│   │   └── jira_synthetic_AIP.json
│   ├── meeting_notes/
│   │   └── meeting_notes.json
│   └── vault.db
├── pyproject.toml
├── src/
│   ├── main.py
│   ├── agent/
│   │   ├── __init__.py
│   │   └── core.py
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── confluence_connector.py
│   │   ├── jira_connector.py
│   │   └── meeting_notes_connector.py
│   ├── memory/
│   │   ├── __init__.py
│   │   └── store.py
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── chroma_store.py
│   │   ├── init_db.py
│   │   └── sqlite_store.py
│   └── tools/
│       ├── __init__.py
│       └── registry.py
└── tests/
    ├── conftest.py
    ├── test_agent.py
    ├── test_chunking.py
    ├── test_confluence_connector.py
    ├── test_ingestion_integration.py
    ├── test_jira_connector.py
    └── test_meeting_notes_connector.py
```

## Setup

1. Install dependencies with Poetry:

   ```
   poetry install
   ```

2. Initialize the SQLite database (optional, `data/vault.db` already exists):

   ```
   poetry run python src/storage/init_db.py
   ```

## Configuration

- `config.py` loads environment variables via `python-dotenv`.
- Set `OPENAI_API_KEY` in your shell or a local `.env` file.
- Default paths: `data/vault.db` for SQLite and `data/chroma/` for ChromaDB.

## Running and Tests

- `src/main.py` is a wiring stub and currently imports modules that are not in the repository yet, so it is not runnable as-is.
- Run tests with:

  ```
  poetry run pytest
  ```

## Data

Synthetic data lives in `data/`:

- Jira: structured issue and metadata JSON files.
- Confluence: `confluence_synthetic.json` pages with linked Jira keys.
- Meeting notes: `meeting_notes.json` with linked Jira items.
