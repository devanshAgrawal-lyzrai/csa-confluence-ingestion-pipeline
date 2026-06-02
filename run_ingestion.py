import argparse
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from confluence_ingestion import db
from confluence_ingestion.confluence_client import ConfluenceClient
from confluence_ingestion.kb_client import KBClient
from confluence_ingestion.lyzr_client import LyzrClient
from confluence_ingestion.pipeline import orchestrate_space


def main():
    parser = argparse.ArgumentParser(
        description="Ingest a Confluence space into the Lyzr Knowledge Base."
    )
    parser.add_argument("--space", required=True, help="Confluence space key, e.g. CKB")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and log everything but skip DB and KB writes.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging.")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    required_env = [
        "CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN",
        "LYZR_API_KEY", "LYZR_AGENT_ID", "LYZR_USER_ID",
        "LYZR_KB_BASE_URL", "LYZR_KB_ID",
        "DATABASE_URL",
    ]
    missing = [k for k in required_env if not os.getenv(k)]
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        print("Copy .env.example to .env and fill in the values.", file=sys.stderr)
        sys.exit(1)

    confluence = ConfluenceClient(
        base_url=os.environ["CONFLUENCE_BASE_URL"],
        email=os.environ["CONFLUENCE_EMAIL"],
        api_token=os.environ["CONFLUENCE_API_TOKEN"],
    )
    lyzr = LyzrClient(
        api_key=os.environ["LYZR_API_KEY"],
        agent_id=os.environ["LYZR_AGENT_ID"],
        user_id=os.environ["LYZR_USER_ID"],
    )
    kb = KBClient(
        base_url=os.environ["LYZR_KB_BASE_URL"],
        kb_id=os.environ["LYZR_KB_ID"],
        api_key=os.environ["LYZR_API_KEY"],
    )

    conn = db.get_db_connection()
    try:
        orchestrate_space(
            space_key=args.space,
            confluence=confluence,
            lyzr=lyzr,
            kb=kb,
            conn=conn,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
