"""One-off: create the Newsletters database in Notion.

Usage:
    python scripts/create_newsletters_db.py <parent_page_id>

The parent page must be shared with the integration whose token is in
NOTION_API_TOKEN. Prints the new database id — paste it into the
NOTION_NEWSLETTERS_DB env var.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings  # noqa: E402  (needs sys.path tweak first)
from notion_client import Client  # noqa: E402


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/create_newsletters_db.py <parent_page_id>")
        sys.exit(1)
    parent_page_id = sys.argv[1]

    notion = Client(auth=get_settings().notion_api_token)

    db = notion.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": "Newsletters"}}],
        initial_data_source={
            "properties": {
                "Name": {"title": {}},
                "Status": {
                    "select": {
                        "options": [
                            {"name": "draft", "color": "yellow"},
                            {"name": "sent", "color": "green"},
                        ]
                    }
                },
                "Subject": {"rich_text": {}},
                "Sent Date": {"date": {}},
                "Sent By": {"rich_text": {}},
                "Channels": {
                    "multi_select": {
                        "options": [
                            {"name": "Google Group", "color": "blue"},
                            {"name": "LCC", "color": "purple"},
                        ]
                    }
                },
            }
        },
    )

    print("Newsletters database created.")
    print(f"Database ID (set as NOTION_NEWSLETTERS_DB): {db['id'].replace('-', '')}")
    print(f"URL: {db.get('url')}")


if __name__ == "__main__":
    main()
