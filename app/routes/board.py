"""Claiming and releasing items on the board.

Both routes answer with the same single-card partial, so htmx can swap the
card in place without a page reload. They are POSTs because they change
something.
"""

import logging
import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import ai, notion
from app.auth import require_user
from app.web import templates

logger = logging.getLogger(__name__)
router = APIRouter()


def _card(request: Request, item: dict, user: str, message: str | None = None):
    return templates.TemplateResponse(
        request,
        "partials/_item_card.html",
        {"item": item, "user": user, "message": message},
    )


@router.post("/items/{page_id}/claim")
async def claim(request: Request, page_id: str, user: str = Depends(require_user)):
    try:
        item = notion.claim_item(page_id, user)
    except Exception:
        logger.exception("Claiming %s failed", page_id)
        return templates.TemplateResponse(
            request,
            "partials/_error.html",
            {"error": "Couldn't reach Notion just then — try again in a moment."},
        )

    # Somebody else got there first.
    if item["owner"] != user:
        return _card(
            request, item, user, message=f"{item['owner'].capitalize()} got there first."
        )
    return _card(request, item, user)


@router.post("/items/{page_id}/release")
async def release(request: Request, page_id: str, user: str = Depends(require_user)):
    try:
        item = notion.release_item(page_id, user)
    except Exception:
        logger.exception("Releasing %s failed", page_id)
        return templates.TemplateResponse(
            request,
            "partials/_error.html",
            {"error": "Couldn't reach Notion just then — try again in a moment."},
        )

    if item["owner"] is not None:
        return _card(request, item, user, message="That one isn't yours to put back.")
    return _card(request, item, user, message="Back on the board.")


# ---------------------------------------------------------------------------
# Adding something by hand
# ---------------------------------------------------------------------------


def _links_from(raw: str) -> list[str]:
    """One URL per line, whitespace and stray punctuation forgiven."""
    out = []
    for line in (raw or "").replace(",", "\n").split("\n"):
        line = line.strip().strip("<>\"'")
        if line.startswith(("http://", "https://")):
            out.append(line)
        elif line and "." in line and " " not in line:
            out.append("https://" + line)
    return out[:8]


@router.get("/items/new")
async def new_item_form(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse(
        request, "item_new.html", {"user": user, "error": None}
    )


@router.post("/items/new/read")
async def read_links(
    request: Request,
    links: str = Form(""),
    note: str = Form(""),
    user: str = Depends(require_user),
):
    """Step 1: read the pasted links and propose field values."""
    urls = _links_from(links)
    if not urls and not note.strip():
        return templates.TemplateResponse(
            request,
            "partials/_error.html",
            {"error": "Paste at least one link, or write a line about what this is."},
        )
    try:
        tags, locations = notion.item_vocabulary()
        suggestion = ai.suggest_item_fields(urls, note.strip(), tags, locations)
    except Exception as e:
        logger.exception("Reading links failed")
        return templates.TemplateResponse(
            request, "partials/_error.html", {"error": f"Couldn't read those links: {e}"}
        )

    return templates.TemplateResponse(
        request,
        "partials/_item_suggestion.html",
        {
            "s": suggestion,
            "user": user,
            "links": urls,
            "note": note.strip(),
            "project_types": ai.PROJECT_TYPES,
            "actions": ai.ACTIONS,
            "priorities": ai.PRIORITIES,
        },
    )


@router.post("/items/new")
async def create_new_item(
    request: Request,
    title: str = Form(...),
    summary: str = Form(""),
    project_type: str = Form("other"),
    action_required: str = Form("monitoring"),
    priority: str = Form("medium"),
    tags: str = Form(""),
    locations: str = Form(""),
    key_points: str = Form(""),
    why_we_care: str = Form(""),
    deadline: str = Form(""),
    main_link: str = Form(""),
    owner: str = Form(""),
    user: str = Depends(require_user),
):
    """Step 2: write it to Notion. Every field is editable before this point."""
    try:
        created = notion.create_item(
            title=title.strip(),
            summary=summary.strip(),
            project_type=project_type,
            action_required=action_required,
            priority=priority,
            tags=[t.strip() for t in tags.split(",") if t.strip()],
            locations=[l.strip() for l in locations.split(",") if l.strip()],
            key_points=key_points.strip(),
            why_we_care=why_we_care.strip(),
            deadline=deadline.strip() or None,
            main_link=main_link.strip() or None,
            owner=owner.strip() or None,
            added_by=user,
        )
    except Exception as e:
        logger.exception("Creating item failed")
        return templates.TemplateResponse(
            request,
            "item_new.html",
            {"user": user, "error": f"Couldn't save it to Notion: {e}"},
            status_code=500,
        )

    logger.info("%s created item %s (%s)", user, title.strip()[:60], created["id"])
    return RedirectResponse(f"/?added={created['id']}", status_code=303)


# ---------------------------------------------------------------------------
# One item in full
# ---------------------------------------------------------------------------
# Registered last on purpose: "/items/{page_id}" would otherwise shadow
# "/items/new", and FastAPI matches in definition order.

_UUID = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")


@router.get("/items/{page_id}")
async def item_page(request: Request, page_id: str, user: str = Depends(require_user)):
    """Everything we hold about one item, in one place."""
    back = request.headers.get("referer") or "/"
    if not _UUID.match(page_id):
        # Not a Notion id at all — don't bother the API with it.
        return templates.TemplateResponse(
            request,
            "item_detail.html",
            {"user": user, "item": None, "error": "That isn't an item.", "back": back},
            status_code=404,
        )
    try:
        item = notion.item_detail(page_id)
    except Exception as e:
        logger.exception("Could not load item %s", page_id)
        return templates.TemplateResponse(
            request,
            "item_detail.html",
            {"user": user, "item": None, "error": f"Couldn't load that item: {e}", "back": back},
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "item_detail.html",
        {"user": user, "item": item, "error": None, "back": back},
    )
