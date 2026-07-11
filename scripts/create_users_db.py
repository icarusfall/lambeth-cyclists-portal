"""One-off: create the Portal Users database in Notion and seed known users.

Usage:
    python scripts/create_users_db.py <parent_page_id>

Rows are created with an empty Password Hash — login falls back to the
PORTAL_USERS env var until each person changes their password in the portal.
Prints the database id for the NOTION_USERS_DB env var.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings  # noqa: E402
from notion_client import Client  # noqa: E402

SEED_USERS = [
    ("charlie", "charlie.ullman@gmail.com"),
    ("colin", "colin@penning.org.uk"),
]


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/create_users_db.py <parent_page_id>")
        sys.exit(1)
    parent_page_id = sys.argv[1]

    notion = Client(auth=get_settings().notion_api_token)

    db = notion.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": "Portal Users"}}],
        initial_data_source={
            "properties": {
                "Name": {"title": {}},
                "Email": {"email": {}},
                "Password Hash": {"rich_text": {}},
            }
        },
    )
    ds_id = db["data_sources"][0]["id"]

    for name, email in SEED_USERS:
        notion.pages.create(
            parent={"type": "data_source_id", "data_source_id": ds_id},
            properties={
                "Name": {"title": [{"type": "text", "text": {"content": name}}]},
                "Email": {"email": email},
            },
        )
        print(f"seeded user: {name} <{email}>")

    print("Portal Users database created.")
    print(f"Database ID (set as NOTION_USERS_DB): {db['id'].replace('-', '')}")
    print(f"URL: {db.get('url')}")


if __name__ == "__main__":
    main()
