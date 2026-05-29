# AI Agent Project

This project is designed to create an AI agent capable of ingesting data from various sources, processing it, and generating reports. The architecture is modular, allowing for easy expansion and maintenance.

## Project Structure

```
ai-agent/
├── src/
│   ├── main.py                     # Entry point for the application
│   ├── agent/
│   │   ├── __init__.py             # Initializes the agent module
│   │   └── core.py                  # Core functionalities of the agent
│   ├── tools/
│   │   ├── __init__.py             # Initializes the tools module
│   │   └── registry.py              # Tool registry for the agent
│   ├── memory/
│   │   ├── __init__.py             # Initializes the memory module
│   │   └── store.py                 # Memory storage functionalities
│   ├── ingestion/
│   │   ├── __init__.py             # Initializes the ingestion module
│   │   ├── jira_connector.py        # Jira connector implementation
│   │   ├── confluence_connector.py   # Confluence connector implementation
│   │   └── meeting_notes_connector.py # Meeting notes ingestion implementation
│   ├── agents/
│   │   ├── __init__.py             # Initializes the agents module
│   │   ├── report_agent.py          # ReportAgent class definition
│   │   └── concern_engine.py        # ConcernEngine class implementation
│   ├── storage/
│   │   ├── __init__.py             # Initializes the storage module
│   │   ├── sqlite_store.py          # SQLiteStore class implementation
│   │   └── chroma_store.py          # ChromaStore class implementation
│   ├── guardrail/
│   │   ├── __init__.py             # Initializes the guardrail module
│   │   ├── sanitizer.py             # Sanitizer class implementation
│   │   └── audit_log.py             # AuditLog class implementation
│   └── mcp/
│       ├── __init__.py             # Initializes the MCP module
│       └── server.py                # MCP server functionality
├── data/                            # Directory for storing ingested data
│   ├── jira/                        # Jira data storage
│   ├── confluence/                  # Confluence data storage
│   └── meeting_notes/               # Meeting notes data storage
├── output/                          # Directory for storing output files
├── tests/
│   └── test_agent.py                # Test files for the agent
├── pyproject.toml                   # Project configuration file
├── .gitignore                       # Git ignore file
├── config.py                        # Configuration settings for the project
├── requirements.txt                 # Project dependencies
└── run_agent.sh                     # Shell script to run the agent
```

## Getting Started

1. **Clone the repository**:

   ```
   git clone <repository-url>
   cd ai-agent
   ```

2. **Install dependencies**:

   ```
   pip install -r requirements.txt
   ```

3. **Run the agent**:
   ```
   bash run_agent.sh
   ```

## Contributing

Contributions are welcome! Please open an issue or submit a pull request for any enhancements or bug fixes.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
