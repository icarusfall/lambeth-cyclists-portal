"""One-off: add the ownership fields to the Items database.

Adds two properties, both additive — no existing data is touched:

    Owner       select  — the portal user who has taken the item on
    Claimed On  date    — when they took it on

Owner is a select holding the portal user's name rather than a Notion
"people" property on purpose: portal users are not necessarily members of
the Notion workspace, and we don't want claiming a consultation to require
a Notion seat.

Usage:
    python scripts/add_ownership_fields.py            # show what would change
    python scripts/add_ownership_fields.py --apply    # make the change
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notion  # noqa: E402
from app.auth import parse_users  # noqa: E402
from app.config import get_settings  # noqa: E402

OWNER = "Owner"
CLAIMED_ON = "Claimed On"

# Seed the select with the people we know about so the options are tidy in the
# Notion UI. Notion creates any other option on first write anyway.
FALLBACK_OWNERS = ["charlie", "colin", "simon"]


def known_owners() -> list[str]:
    """Portal user names, from the Notion Portal Users DB where available."""
    names = set(FALLBACK_OWNERS)
    names.update(parse_users().keys())
    try:
        ds = notion.ds_id_for(get_settings().notion_users_db)
        rows = notion.client().data_sources.query(data_source_id=ds).get("results", [])
        names.update(
            t.strip().lower()
            for t in (notion.get_page_title(p) for p in rows)
            if t and t.strip()
        )
    except Exception as e:
        print(f"  (could not read Portal Users DB, using defaults: {e})")
    return sorted(names)


def main():
    apply = "--apply" in sys.argv
    settings = get_settings()
    items_db = settings.notion_items_db

    if not settings.notion_api_token:
        sys.exit("NOTION_API_TOKEN is not set.")

    ds_id = notion.ds_id_for(items_db)
    ds = notion.client().data_sources.retrieve(data_source_id=ds_id)
    existing = ds.get("properties", {})

    print(f"Items data source: {ds_id}")
    print(f"{len(existing)} existing properties\n")

    owners = known_owners()
    to_add = {}

    if OWNER in existing:
        print(f"  '{OWNER}' already exists ({existing[OWNER]['type']}) — leaving alone")
    else:
        to_add[OWNER] = {"select": {"options": [{"name": n} for n in owners]}}
        print(f"  will add '{OWNER}' (select) with options: {', '.join(owners)}")

    if CLAIMED_ON in existing:
        print(f"  '{CLAIMED_ON}' already exists — leaving alone")
    else:
        to_add[CLAIMED_ON] = {"date": {}}
        print(f"  will add '{CLAIMED_ON}' (date)")

    if not to_add:
        print("\nNothing to do — both fields are already present.")
        return

    if not apply:
        print("\nDry run. Re-run with --apply to make these changes.")
        return

    notion.client().data_sources.update(data_source_id=ds_id, properties=to_add)
    print(f"\nAdded {len(to_add)} property/properties to the Items database.")
    print("Nothing else changed; every existing item now has an empty Owner,")
    print("which is what puts it in the 'Needs someone' queue on the board.")


if __name__ == "__main__":
    main()
