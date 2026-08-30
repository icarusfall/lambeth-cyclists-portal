"""Claiming and releasing items on the board.

Both routes answer with the same single-card partial, so htmx can swap the
card in place without a page reload. They are POSTs because they change
something.
"""

import logging

from fastapi import APIRouter, Depends, Request

from app import notion
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
