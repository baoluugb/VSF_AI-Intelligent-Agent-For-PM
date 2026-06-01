import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

# Add the project root to sys.path so 'src' and 'config' can be imported
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.ingestion.jira_connector import JiraConnector
from src.ingestion.confluence_connector import ConfluenceConnector
from src.ingestion.meeting_notes_connector import MeetingNotesConnector
from src.ingestion.entity_extractor import EntityExtractor
from src.storage.sqlite_store import SQLiteStore
from src.storage.chroma_store import ChromaStore
from src.storage.init_db import init_db
from config import CHROMA_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

def run_pipeline(jira_path: str, conf_path: str, notes_path: str) -> None:
    """Run the ingestion pipeline across all three sources and route to stores."""
    # Ensure database tables exist
    logger.info("Initializing database...")
    init_db()
    
    all_docs: List[Dict[str, Any]] = []

    # 1. Load data from connectors
    if jira_path:
        logger.info(f"Loading Jira from {jira_path}")
        jira_conn = JiraConnector(jira_path)
        all_docs.extend(jira_conn.load())
    
    if conf_path:
        logger.info(f"Loading Confluence from {conf_path}")
        conf_conn = ConfluenceConnector(conf_path)
        all_docs.extend(conf_conn.load())
        
    if notes_path:
        logger.info(f"Loading Meeting Notes from {notes_path}")
        notes_conn = MeetingNotesConnector(notes_path)
        all_docs.extend(notes_conn.load())
        
    logger.info(f"Loaded total {len(all_docs)} normalized documents.")
    
    # 2. Extract Entities and Backlinks
    extractor = EntityExtractor()
    entities, backlinks = extractor.extract(all_docs)
    logger.info(f"Extracted {len(entities)} entities and {len(backlinks)} backlinks.")
    
    # 3. Route to SQLite (Structured Data)
    with SQLiteStore() as sqlite_store:
        sqlite_store.bulk_upsert(entities)
        
        # Save snapshot for each entity for the day-over-day diff
        for entity in entities:
            task_id = entity.get("task_id")
            if task_id:
                sqlite_store.save_snapshot(task_id, entity, None)
                
        sqlite_store.insert_backlinks(backlinks)
        
        # Update sync logs
        sources = {doc.get("source") for doc in all_docs if doc.get("source")}
        for source in sources:
            sqlite_store.update_sync_log(source)
            
    logger.info("Saved structured data to SQLite.")

    # 4. Route to ChromaDB (Semantic/Vector Data)
    chroma_store = ChromaStore(path=CHROMA_PATH)
    jira_chunks = 0
    conf_chunks = 0
    meet_chunks = 0

    for doc in all_docs:
        source = doc.get("source")
        if source == "jira":
            chroma_store.add_jira_description(doc)
            jira_chunks += 1
        elif source == "confluence":
            conf_chunks += chroma_store.add_confluence_chunks(doc)
        elif source == "meeting_notes":
            meet_chunks += chroma_store.add_meeting_chunks(doc)
            
    logger.info(f"Saved semantic data to ChromaDB (Jira docs: {jira_chunks}, Confluence chunks: {conf_chunks}, Meeting chunks: {meet_chunks}).")
    logger.info("Ingestion pipeline completed successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion Orchestrator Pipeline")
    parser.add_argument("--jira", type=str, help="Path to Jira JSON file", default="")
    parser.add_argument("--conf", type=str, help="Path to Confluence JSON folder", default="")
    parser.add_argument("--notes", type=str, help="Path to Meeting Notes folder", default="")
    args = parser.parse_args()

    if not args.jira and not args.conf and not args.notes:
        logger.error("No input paths provided. Use --jira, --conf, or --notes.")
        sys.exit(1)

    try:
        run_pipeline(args.jira, args.conf, args.notes)
    except Exception as e:
        logger.exception("Pipeline failed:")
        sys.exit(1)

if __name__ == "__main__":
    main()
