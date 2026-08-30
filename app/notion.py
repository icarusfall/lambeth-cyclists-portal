"""Notion access layer.

Query/property-extraction patterns ported from lambeth-cyclists-mcp/server.py.
Uses the notion-client v3 data_sources API: each database has a db_id, and a
ds_id (data source) used for queries — discovered from the db_id and cached.
"""

import json
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
    """Projects worth mentioning in a newsletter — live or being planned."""
    results = query(
        get_settings().notion_projects_db,
        filter_obj={
            "or": [
                {"property": "Status", "select": {"equals": "active"}},
                {"property": "Status", "select": {"equals": "planning"}},
            ]
        },
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


def discard_newsletter(page_id: str):
    """Move a newsletter page to Notion's trash (recoverable there for ~30 days)."""
    client().pages.update(page_id=page_id, in_trash=True)


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


# ---------------------------------------------------------------------------
# Portal users (Name, Email, Password Hash) — lets people change their own
# password; the PORTAL_USERS env var remains as bootstrap/fallback.
# ---------------------------------------------------------------------------


def get_portal_user(name: str) -> dict | None:
    """Return {page_id, name, email, password_hash} for a user, or None."""
    db_id = get_settings().notion_users_db
    if not db_id:
        return None
    results = query(
        db_id,
        filter_obj={"property": "Name", "title": {"equals": name.strip().lower()}},
        limit=1,
    )
    if not results:
        return None
    simple = simplify_page(results[0])
    return {
        "page_id": simple["id"],
        "name": simple["title"],
        "email": simple["props"].get("Email", ""),
        "password_hash": simple["props"].get("Password Hash", ""),
    }


def set_portal_user_password(name: str, password_hash: str):
    """Write a user's new bcrypt hash, creating their row if needed."""
    name = name.strip().lower()
    existing = get_portal_user(name)
    properties = {
        "Password Hash": {
            "rich_text": [{"type": "text", "text": {"content": password_hash}}]
        }
    }
    if existing:
        client().pages.update(page_id=existing["page_id"], properties=properties)
        return
    db_id = get_settings().notion_users_db
    properties["Name"] = {"title": [{"type": "text", "text": {"content": name}}]}
    client().pages.create(
        parent={"type": "data_source_id", "data_source_id": ds_id_for(db_id)},
        properties=properties,
    )


def current_drafts(limit: int = 10) -> list[dict]:
    """All draft-status newsletters, newest first (dashboard lists them)."""
    results = query(
        get_settings().notion_newsletters_db,
        filter_obj={
            "property": "Status",
            "select": {"equals": NEWSLETTER_STATUS_DRAFT},
        },
        sorts=[{"timestamp": "created_time", "direction": "descending"}],
        limit=limit,
    )
    return [simplify_page(p) for p in results]


# ---------------------------------------------------------------------------
# Ownership: claiming items off the board
# ---------------------------------------------------------------------------
# "Owner" is a select holding a portal user name, not a Notion people
# property — claiming a consultation must not require a Notion seat.
# Run scripts/add_ownership_fields.py once to create the fields.

OWNER_PROP = "Owner"
CLAIMED_ON_PROP = "Claimed On"

# Items in these states are finished; they never want an owner.
_DONE_STATUSES = ("submitted", "closed")


def _live_item_filters() -> list[dict]:
    """Filters excluding items that are already finished with."""
    return [
        {"property": "Status", "select": {"does_not_equal": s}} for s in _DONE_STATUSES
    ]


def item_owner(page) -> str | None:
    """The portal user who owns a raw Notion page, or None."""
    prop = page.get("properties", {}).get(OWNER_PROP) or {}
    sel = prop.get("select")
    return (sel or {}).get("name") or None


# Only these actually need a person. Everything else the processor files is
# for the record — showing it on the board buries the real asks.
_NEEDS_A_PERSON = ("response_needed", "urgent_action")


def unclaimed_items(limit: int = 5, within_days: int = 90) -> list[dict]:
    """Live items nobody has taken on — the 'Needs someone' queue.

    Deliberately a short list, not the whole backlog. A volunteer offered
    twenty undifferentiated items reads it as a wall and picks none of them;
    the point of the board is a handful of specific asks. `limit` is the
    number shown, so raise it only alongside a "see all" page.

    Excluded: items that only need filing (information_only, monitoring),
    consultations whose deadline has already passed, and anything that
    arrived more than `within_days` ago without a deadline — six months of
    untriaged history is not a to-do list.

    The date rule is applied in Python: expressing it as a filter needs
    and-inside-or-inside-and, and Notion only nests two levels deep.
    """
    results = query(
        get_settings().notion_items_db,
        filter_obj={
            "and": [
                {"property": OWNER_PROP, "select": {"is_empty": True}},
                *_live_item_filters(),
                {
                    "or": [
                        {"property": "Action Required", "select": {"equals": a}}
                        for a in _NEEDS_A_PERSON
                    ]
                },
            ]
        },
        sorts=[{"property": "Date Received", "direction": "descending"}],
        limit=100,
    )

    today = date.today()
    cutoff = today - timedelta(days=within_days)
    keep = []
    for raw in results:
        deadline = get_date_prop(raw, "Consultation Deadline")
        if deadline is not None:
            if deadline < today:
                continue  # the moment to respond has gone
        else:
            received = get_date_prop(raw, "Date Received")
            if received is not None and received < cutoff:
                continue  # old and undated; triage, not a live job
        item = simplify_page(raw)
        item["deadline"] = deadline
        item["owner"] = None
        keep.append(item)

    # Soonest deadline first, undated last, newest first within each group.
    keep.sort(key=lambda i: (i["deadline"] is None, i["deadline"] or today))
    return keep[:limit]


def items_owned_by(user: str, limit: int = 20) -> list[dict]:
    """Live items this person has taken on — the 'You're on' list."""
    results = query(
        get_settings().notion_items_db,
        filter_obj={
            "and": [
                {"property": OWNER_PROP, "select": {"equals": user}},
                *_live_item_filters(),
            ]
        },
        sorts=[{"property": "Consultation Deadline", "direction": "ascending"}],
        limit=limit,
    )
    items = [simplify_page(p) for p in results]
    for item, raw in zip(items, results):
        item["deadline"] = get_date_prop(raw, "Consultation Deadline")
        item["owner"] = user
    return items


def get_item(page_id: str) -> dict:
    """One item, flattened, with deadline and owner resolved."""
    page = client().pages.retrieve(page_id=page_id)
    item = simplify_page(page)
    item["deadline"] = get_date_prop(page, "Consultation Deadline")
    item["owner"] = item_owner(page)
    return item


def claim_item(page_id: str, user: str) -> dict:
    """Take an item on. Returns the item as it now stands.

    If somebody else got there first this makes no change and returns their
    version, so the caller can tell the user who has it. Notion has no
    conditional write, so this is a read-then-write check rather than a true
    lock — fine for a committee, not for a crowd.
    """
    current = get_item(page_id)
    if current["owner"] and current["owner"] != user:
        return current

    client().pages.update(
        page_id=page_id,
        properties={
            OWNER_PROP: {"select": {"name": user}},
            CLAIMED_ON_PROP: {"date": {"start": date.today().isoformat()}},
        },
    )
    current["owner"] = user
    return current


def release_item(page_id: str, user: str) -> dict:
    """Put an item back on the board. Only its owner may do this."""
    current = get_item(page_id)
    if current["owner"] != user:
        return current

    client().pages.update(
        page_id=page_id,
        properties={
            OWNER_PROP: {"select": None},
            CLAIMED_ON_PROP: {"date": None},
        },
    )
    current["owner"] = None
    return current


# ---------------------------------------------------------------------------
# Creating an item by hand
# ---------------------------------------------------------------------------


def _multi_select_options(db_id: str, field: str) -> list[str]:
    """Current option names for a multi-select, for reuse in AI suggestions."""
    ds = client().data_sources.retrieve(data_source_id=ds_id_for(db_id))
    prop = ds.get("properties", {}).get(field) or {}
    return [o["name"] for o in prop.get("multi_select", {}).get("options", [])]


def item_vocabulary() -> tuple[list[str], list[str]]:
    """(tags, locations) already in use on the Items database.

    Fed to the model so a hand-added item reuses the vocabulary the email
    pipeline built up, instead of spawning near-duplicate options.
    """
    db = get_settings().notion_items_db
    return _multi_select_options(db, "Tags"), _multi_select_options(db, "Locations")


def create_item(
    *,
    title: str,
    summary: str,
    project_type: str,
    action_required: str,
    priority: str,
    tags: list[str],
    locations: list[str],
    key_points: str,
    why_we_care: str,
    deadline: str | None = None,
    main_link: str | None = None,
    owner: str | None = None,
    added_by: str = "",
) -> dict:
    """Create an Items page from the portal. Returns {id, url}.

    Processing Status is needs_review, not ai_complete: a person pasted some
    links and a model read them, so the entry deserves a second look before
    it is treated as settled.
    """
    props: dict = {
        "Title": {"title": [{"type": "text", "text": {"content": title[:200]}}]},
        "Summary": {"rich_text": [{"type": "text", "text": {"content": summary[:2000]}}]},
        "Date Received": {"date": {"start": date.today().isoformat()}},
        "Project Type": {"select": {"name": project_type}},
        "Action Required": {"select": {"name": action_required}},
        "Priority": {"select": {"name": priority}},
        "Status": {"select": {"name": "new"}},
        "Processing Status": {"select": {"name": "needs_review"}},
        "Has Attachments": {"checkbox": False},
    }
    if tags:
        props["Tags"] = {"multi_select": [{"name": t[:100]} for t in tags[:12]]}
    if locations:
        props["Locations"] = {"multi_select": [{"name": l[:100]} for l in locations[:12]]}
    if key_points:
        props["AI Key Points"] = {"rich_text": [{"type": "text", "text": {"content": key_points[:2000]}}]}

    thoughts = why_we_care
    if added_by:
        thoughts = f"{thoughts}\n\nAdded by hand via the portal by {added_by}.".strip()
    if thoughts:
        props["Lambeth Cyclist Thoughts"] = {
            "rich_text": [{"type": "text", "text": {"content": thoughts[:2000]}}]
        }

    if deadline:
        props["Consultation Deadline"] = {"date": {"start": deadline}}
    if main_link:
        props["Link to Consultation"] = {"url": main_link}
    if owner:
        props[OWNER_PROP] = {"select": {"name": owner}}
        props[CLAIMED_ON_PROP] = {"date": {"start": date.today().isoformat()}}

    page = client().pages.create(
        parent={"type": "data_source_id", "data_source_id": ds_id_for(get_settings().notion_items_db)},
        properties=props,
    )
    return {"id": page["id"], "url": page.get("url")}


# ---------------------------------------------------------------------------
# Triage: leads -> projects
# ---------------------------------------------------------------------------


def untriaged_items(limit: int = 60) -> list[dict]:
    """Items nobody has sorted yet: still 'new' and attached to no project."""
    results = query(
        get_settings().notion_items_db,
        filter_obj={
            "and": [
                {"property": "Status", "select": {"equals": "new"}},
                {"property": "Related Project", "relation": {"is_empty": True}},
            ]
        },
        sorts=[{"property": "Date Received", "direction": "descending"}],
        limit=limit,
    )
    out = []
    for n, raw in enumerate(results, start=1):
        item = simplify_page(raw)
        item["number"] = n
        item["received"] = str(get_date_prop(raw, "Date Received") or "")
        item["project_type"] = item["props"].get("Project Type")
        locs = raw["properties"].get("Locations", {}).get("multi_select", [])
        item["locations"] = [o["name"] for o in locs]
        item["summary"] = item["props"].get("Summary", "")
        out.append(item)
    return out


def project_titles() -> list[str]:
    return [get_page_title(p) for p in query(get_settings().notion_projects_db, limit=50)]


def find_project_by_title(title: str) -> str | None:
    for p in query(get_settings().notion_projects_db, limit=50):
        if get_page_title(p).strip().lower() == title.strip().lower():
            return p["id"]
    return None



# Multi-select options are permanent once created, so a vague catch-all like
# "Various roads, borough-wide" pollutes the vocabulary for good. Geographic
# Scope already records breadth; Primary Locations should be real places.
_NOT_PLACES = ("various", "borough-wide", "borough wide", "multiple", "several",
               "n/a", "none", "tbc", "unknown", "-")


def _is_a_place(name: str) -> bool:
    low = name.strip().lower()
    return bool(low) and not any(bad in low for bad in _NOT_PLACES)


def create_project(
    *,
    title: str,
    description: str,
    project_type: str,
    geographic_scope: str,
    priority: str,
    primary_locations: list[str],
    next_action: str,
) -> dict:
    """Create a Projects page. Status starts at 'planning'."""
    props: dict = {
        "Project Name": {"title": [{"type": "text", "text": {"content": title[:200]}}]},
        "Description": {"rich_text": [{"type": "text", "text": {"content": description[:2000]}}]},
        "Project Type": {"select": {"name": project_type}},
        "Geographic Scope": {"select": {"name": geographic_scope}},
        "Priority": {"select": {"name": priority}},
        "Status": {"select": {"name": "planning"}},
        "Start Date": {"date": {"start": date.today().isoformat()}},
    }
    places = [l for l in (x.strip() for x in primary_locations) if _is_a_place(l)]
    if places:
        props["Primary Locations"] = {"multi_select": [{"name": l[:100]} for l in places[:12]]}
    if next_action:
        props["Next Action"] = {"rich_text": [{"type": "text", "text": {"content": next_action[:2000]}}]}

    page = client().pages.create(
        parent={
            "type": "data_source_id",
            "data_source_id": ds_id_for(get_settings().notion_projects_db),
        },
        properties=props,
    )
    return {"id": page["id"], "url": page.get("url")}


def attach_items_to_project(item_ids: list[str], project_id: str) -> int:
    """Link items to a project and mark them as sorted."""
    done = 0
    for item_id in item_ids:
        try:
            client().pages.update(
                page_id=item_id,
                properties={
                    "Related Project": {"relation": [{"id": project_id}]},
                    "Status": {"select": {"name": "reviewed"}},
                },
            )
            done += 1
        except Exception:
            logger.exception("Could not attach item %s to project %s", item_id, project_id)
    return done


def set_items_not_relevant(item_ids: list[str]) -> int:
    """Mark items as needing no project, so they drop out of the triage queue."""
    done = 0
    for item_id in item_ids:
        try:
            client().pages.update(
                page_id=item_id, properties={"Status": {"select": {"name": "closed"}}}
            )
            done += 1
        except Exception:
            logger.exception("Could not close item %s", item_id)
    return done


# Standing ("sweep") projects are permanent catch-alls for recurring routine
# work — CPZs, dockless bays, bus priority corridors. Lambeth always has some
# on the go, and each one individually is not worth its own project. The
# convention is a description that opens with this marker, which is what tells
# triage the project actively wants matching items swept into it.
STANDING_MARKER = "Standing project."


def projects_for_matching() -> list[dict]:
    """Every project with enough context for triage to match against.

    Titles alone are not enough: 'Controlled Parking Zones' only works as a
    sweep if the model can read that it exists to absorb every CPZ notice.
    """
    out = []
    for p in query(get_settings().notion_projects_db, limit=60):
        props = p.get("properties", {})
        desc = rich_text_to_str(props.get("Description", {}).get("rich_text", []))
        out.append(
            {
                "title": get_page_title(p),
                "description": desc,
                "type": (props.get("Project Type", {}).get("select") or {}).get("name") or "",
                "status": (props.get("Status", {}).get("select") or {}).get("name") or "",
                "standing": desc.strip().startswith(STANDING_MARKER),
            }
        )
    return out


# ---------------------------------------------------------------------------
# One item, in full
# ---------------------------------------------------------------------------


def _attachments(raw_value: str) -> list[dict]:
    """Attachment URLs is stored as a JSON array of {filename, url}."""
    if not raw_value.strip():
        return []
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        # Older rows stored a bare URL or a comma-separated list.
        return [{"filename": u.strip(), "url": u.strip()}
                for u in raw_value.split(",") if u.strip().startswith("http")]
    if isinstance(parsed, dict):
        parsed = [parsed]
    return [a for a in parsed if isinstance(a, dict) and a.get("url")]


def item_detail(page_id: str) -> dict:
    """Everything we hold about one item, shaped for the detail page."""
    page = client().pages.retrieve(page_id=page_id)
    props = page.get("properties", {})

    def rt(name):
        return rich_text_to_str(props.get(name, {}).get("rich_text", []))

    def sel(name):
        return (props.get(name, {}).get("select") or {}).get("name")

    def ms(name):
        return [o["name"] for o in props.get(name, {}).get("multi_select", [])]

    summary, key_points = rt("Summary"), rt("AI Key Points")
    gmail_id = rt("Gmail Message ID")

    # The processor writes these when a Claude call fails, then carries on. They
    # look like real content in a list view, so say plainly what they are.
    analysis_failed = (
        "Error analyzing email content" in summary
        or "Error during AI analysis" in key_points
    )

    related = props.get("Related Project", {}).get("relation", [])
    project = None
    if related:
        try:
            project_page = client().pages.retrieve(page_id=related[0]["id"])
            project = {"title": get_page_title(project_page), "url": project_page.get("url")}
        except Exception:
            logger.exception("Could not resolve related project for %s", page_id)

    return {
        "id": page["id"],
        "url": page.get("url"),
        "title": get_page_title(page),
        "summary": summary,
        "key_points": key_points,
        "thoughts": rt("Lambeth Cyclist Thoughts"),
        "attachment_analysis": rt("Attachment Analysis"),
        "analysis_failed": analysis_failed,
        "owner": sel("Owner"),
        "status": sel("Status"),
        "action_required": sel("Action Required"),
        "priority": sel("Priority"),
        "project_type": sel("Project Type"),
        "processing_status": sel("Processing Status"),
        "tags": ms("Tags"),
        "locations": ms("Locations"),
        "received": get_date_prop(page, "Date Received"),
        "deadline": get_date_prop(page, "Consultation Deadline"),
        "action_due": get_date_prop(page, "Action Due Date"),
        "claimed_on": get_date_prop(page, "Claimed On"),
        "sender": (props.get("Sender Email", {}) or {}).get("email"),
        "gmail_id": gmail_id,
        # Opens the original in whichever Gmail account the reader is signed
        # into — useful for Charlie, a dead end for everyone else, so the
        # template labels it accordingly.
        "gmail_url": f"https://mail.google.com/mail/u/0/#all/{gmail_id}" if gmail_id else None,
        "consultation_url": (props.get("Link to Consultation", {}) or {}).get("url"),
        "attachments": _attachments(rt("Attachment URLs")),
        "project": project,
    }


# ---------------------------------------------------------------------------
# Is the email pipeline actually working?
# ---------------------------------------------------------------------------
# The processor writes placeholder text and carries on when a Claude call
# fails, so a broken pipeline looks identical to a quiet one from the outside:
# items keep arriving, they just say nothing. That went unnoticed for six
# weeks. This is the check that makes it visible to everyone, not just to
# whoever reads the Railway alerts.

_FAILURE_MARKERS = ("Error analyzing email content", "Error during AI analysis")

# Mail arrives roughly fortnightly, so a fortnight of silence is normal and
# three weeks is worth a look — long enough not to cry wolf over a quiet spell.
QUIET_DAYS = 21


def pipeline_health(sample: int = 60) -> dict:
    """A verdict on the email pipeline, from the items it has produced."""
    rows = query(
        get_settings().notion_items_db,
        sorts=[{"property": "Date Received", "direction": "descending"}],
        limit=sample,
    )

    failed, newest, newest_good = [], None, None
    for page in rows:
        props = page.get("properties", {})
        blob = rich_text_to_str(props.get("Summary", {}).get("rich_text", [])) + rich_text_to_str(
            props.get("AI Key Points", {}).get("rich_text", [])
        )
        received = get_date_prop(page, "Date Received")
        if received and (newest is None or received > newest):
            newest = received

        if any(m in blob for m in _FAILURE_MARKERS):
            failed.append({"id": page["id"], "title": get_page_title(page), "received": received})
        elif received and (newest_good is None or received > newest_good):
            newest_good = received

    quiet_for = (date.today() - newest).days if newest else None

    # Recency matters more than the count: five failures from July that have
    # since been fixed are history, five from this week are an outage.
    recent_failures = [
        f for f in failed if f["received"] and (date.today() - f["received"]).days <= 30
    ]

    if recent_failures:
        state = "broken"
    elif quiet_for is not None and quiet_for > QUIET_DAYS:
        state = "quiet"
    else:
        state = "ok"

    return {
        "state": state,
        "failed": sorted(failed, key=lambda f: f["received"] or date.min, reverse=True),
        "recent_failures": len(recent_failures),
        "newest": newest,
        "newest_good": newest_good,
        "quiet_for": quiet_for,
        "quiet_days": QUIET_DAYS,
    }
