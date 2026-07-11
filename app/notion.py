"""Notion access layer.

Query/property-extraction patterns ported from lambeth-cyclists-mcp/server.py.
Uses the notion-client v3 data_sources API: each database has a db_id, and a
ds_id (data source) used for queries — discovered from the db_id and cached.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from notion_client import Client

from app.config import get_settings

logger = logging.getLogger(__name__)

NEWSLETTER_STATUS_DRAFT = "draft"
NEWSLETTER_STATUS_SENT = "sent"

# rich_text objects are capped at 2000 chars by the Notion API
TEXT_CHUNK = 2000


@lru_cache
def client() -> Client:
    return Client(auth=get_settings().notion_api_token)


_ds_cache: dict[str, str] = {}


def ds_id_for(db_id: str) -> str:
    """Resolve (and cache) the data-source id for a database id."""
    if db_id not in _ds_cache:
        db = client().databases.retrieve(database_id=db_id)
        _ds_cache[db_id] = db["data_sources"][0]["id"]
    return _ds_cache[db_id]


# ---------------------------------------------------------------------------
# Property extraction (ported from the MCP server)
# ---------------------------------------------------------------------------


def rich_text_to_str(rt_array) -> str:
    return "".join(seg.get("plain_text", "") for seg in rt_array)


def extract_property_value(prop):
    """Return a human-readable value from a Notion property object."""
    t = prop["type"]

    if t == "title":
        return rich_text_to_str(prop["title"])
    if t == "rich_text":
        return rich_text_to_str(prop["rich_text"])
    if t == "number":
        return str(prop["number"]) if prop["number"] is not None else None
    if t == "select":
        return prop["select"]["name"] if prop["select"] else None
    if t == "multi_select":
        return ", ".join(s["name"] for s in prop["multi_select"]) or None
    if t == "date":
        d = prop["date"]
        if not d:
            return None
        start = d.get("start", "")
        end = d.get("end")
        return f"{start} to {end}" if end else start
    if t == "checkbox":
        return "Yes" if prop["checkbox"] else "No"
    if t == "url":
        return prop["url"]
    if t == "email":
        return prop["email"]
    if t == "phone_number":
        return prop["phone_number"]
    if t == "people":
        names = [p.get("name", "Unknown") for p in prop["people"]]
        return ", ".join(names) if names else None
    if t == "relation":
        n = len(prop["relation"])
        return f"({n} linked)" if n else None
    if t == "formula":
        f = prop["formula"]
        return str(f.get(f["type"]))
    if t == "status":
        return prop["status"]["name"] if prop["status"] else None
    if t == "created_time":
        return prop["created_time"]
    if t == "last_edited_time":
        return prop["last_edited_time"]
    return None


def get_page_title(page) -> str:
    for prop in page.get("properties", {}).values():
        if prop["type"] == "title":
            return rich_text_to_str(prop["title"]) or "Untitled"
    return "Untitled"


def get_date_prop(page, name: str) -> date | None:
    """Return the start date of a date property as a date object, if set."""
    prop = page.get("properties", {}).get(name)
    if not prop or prop.get("type") != "date" or not prop.get("date"):
        return None
    start = prop["date"].get("start", "")
    try:
        return datetime.fromisoformat(start.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def simplify_page(page) -> dict:
    """Flatten a Notion page into {id, title, url, props} for templates."""
    props = {}
    for name, prop in page.get("properties", {}).items():
        if prop["type"] == "title":
            continue
        value = extract_property_value(prop)
        if value is not None and str(value).strip():
            props[name] = value
    return {
        "id": page["id"],
        "title": get_page_title(page),
        "url": page.get("url"),
        "props": props,
    }


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def query(db_id: str, filter_obj=None, sorts=None, limit: int | None = None) -> list:
    kwargs = {"data_source_id": ds_id_for(db_id)}
    if filter_obj:
        kwargs["filter"] = filter_obj
    if sorts:
        kwargs["sorts"] = sorts
    if limit:
        kwargs["page_size"] = min(limit, 100)
    response = client().data_sources.query(**kwargs)
    return response.get("results", [])


def upcoming_meetings(limit: int = 5) -> list[dict]:
    """Future meetings, soonest first. Empty list => nothing diarised (dashboard warns)."""
    results = query(
        get_settings().notion_meetings_db,
        filter_obj={
            "property": "Meeting Date",
            "date": {"on_or_after": date.today().isoformat()},
        },
        sorts=[{"property": "Meeting Date", "direction": "ascending"}],
        limit=limit,
    )
    return [simplify_page(p) for p in results]


def recent_items(days: int = 30, limit: int = 50) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    results = query(
        get_settings().notion_items_db,
        filter_obj={"property": "Date Received", "date": {"on_or_after": since}},
        sorts=[{"property": "Date Received", "direction": "descending"}],
        limit=limit,
    )
    return [simplify_page(p) for p in results]


def items_with_deadlines(within_days: int = 60) -> list[dict]:
    """Items whose consultation deadline is today..N days out, soonest first."""
    results = query(
        get_settings().notion_items_db,
        filter_obj={
            "and": [
                {
                    "property": "Consultation Deadline",
                    "date": {"on_or_after": date.today().isoformat()},
                },
                {
                    "property": "Consultation Deadline",
                    "date": {
                        "on_or_before": (
                            date.today() + timedelta(days=within_days)
                        ).isoformat()
                    },
                },
            ]
        },
        sorts=[{"property": "Consultation Deadline", "direction": "ascending"}],
        limit=20,
    )
    return [simplify_page(p) for p in results]


def active_projects() -> list[dict]:
    results = query(
        get_settings().notion_projects_db,
        filter_obj={"property": "Status", "select": {"equals": "active"}},
        limit=20,
    )
    return [simplify_page(p) for p in results]


# ---------------------------------------------------------------------------
# Newsletters database (drafts + sent archive)
# ---------------------------------------------------------------------------
# The newsletter body is stored as a single markdown code block on the page:
# exact round-trip for editing in the portal, still readable in Notion.


def _body_blocks(markdown_body: str) -> list[dict]:
    chunks = [
        markdown_body[i : i + TEXT_CHUNK]
        for i in range(0, len(markdown_body), TEXT_CHUNK)
    ] or [""]
    return [
        {
            "object": "block",
            "type": "code",
            "code": {
                "language": "markdown",
                "rich_text": [
                    {"type": "text", "text": {"content": c}} for c in chunks
                ],
            },
        }
    ]


def save_newsletter_draft(
    title: str, subject: str, markdown_body: str, page_id: str | None = None
) -> str:
    """Create or update a draft newsletter page. Returns the page id."""
    db_id = get_settings().notion_newsletters_db
    properties = {
        "Name": {"title": [{"type": "text", "text": {"content": title}}]},
        "Subject": {
            "rich_text": [{"type": "text", "text": {"content": subject}}]
        },
        "Status": {"select": {"name": NEWSLETTER_STATUS_DRAFT}},
    }

    if page_id:
        client().pages.update(page_id=page_id, properties=properties)
        # Replace existing body blocks
        existing = client().blocks.children.list(block_id=page_id, page_size=100)
        for block in existing.get("results", []):
            client().blocks.delete(block_id=block["id"])
        client().blocks.children.append(
            block_id=page_id, children=_body_blocks(markdown_body)
        )
        return page_id

    page = client().pages.create(
        parent={"type": "data_source_id", "data_source_id": ds_id_for(db_id)},
        properties=properties,
        children=_body_blocks(markdown_body),
    )
    return page["id"]


def load_newsletter(page_id: str) -> dict:
    """Return {id, title, subject, status, markdown, ...props} for a newsletter page."""
    page = client().pages.retrieve(page_id=page_id)
    simple = simplify_page(page)
    blocks = client().blocks.children.list(block_id=page_id, page_size=100)
    markdown_body = ""
    for block in blocks.get("results", []):
        if block["type"] == "code":
            markdown_body += rich_text_to_str(block["code"]["rich_text"])
        elif block["type"] == "paragraph":
            markdown_body += rich_text_to_str(block["paragraph"]["rich_text"]) + "\n\n"
    return {
        "id": page_id,
        "title": simple["title"],
        "subject": simple["props"].get("Subject", simple["title"]),
        "status": simple["props"].get("Status", NEWSLETTER_STATUS_DRAFT),
        "markdown": markdown_body,
        "props": simple["props"],
        "url": simple["url"],
    }


def mark_newsletter_sent(page_id: str, sent_by: str, channels: list[str]):
    client().pages.update(
        page_id=page_id,
        properties={
            "Status": {"select": {"name": NEWSLETTER_STATUS_SENT}},
            "Sent Date": {
                "date": {"start": datetime.now(timezone.utc).isoformat()}
            },
            "Sent By": {
                "rich_text": [{"type": "text", "text": {"content": sent_by}}]
            },
            "Channels": {"multi_select": [{"name": c} for c in channels]},
        },
    )


def list_newsletters(limit: int = 50) -> list[dict]:
    results = query(
        get_settings().notion_newsletters_db,
        sorts=[{"timestamp": "created_time", "direction": "descending"}],
        limit=limit,
    )
    return [simplify_page(p) for p in results]


def current_draft() -> dict | None:
    """Most recent draft-status newsletter, if any (dashboard shows it)."""
    results = query(
        get_settings().notion_newsletters_db,
        filter_obj={
            "property": "Status",
            "select": {"equals": NEWSLETTER_STATUS_DRAFT},
        },
        sorts=[{"timestamp": "created_time", "direction": "descending"}],
        limit=1,
    )
    return simplify_page(results[0]) if results else None
