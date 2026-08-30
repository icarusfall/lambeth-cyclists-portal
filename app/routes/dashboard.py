import logging

from fastapi import APIRouter, Depends, Request

from app import notion
from app.auth import require_user
from app.web import templates

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def home(request: Request, user: str = Depends(require_user)):
    error = None
    meetings, recent, drafts = [], [], []
    health = None
    unclaimed, mine = [], []
    try:
        meetings = notion.upcoming_meetings()
        recent = notion.recent_items(days=30)
        drafts = notion.current_drafts()
    except Exception as e:
        logger.exception("Dashboard Notion queries failed")
        error = f"Couldn't load data from Notion: {e}"

    # Whether the pipeline is reading what it files. Never fatal to the page:
    # a health check that takes the dashboard down with it is worse than none.
    try:
        health = notion.pipeline_health()
    except Exception:
        logger.exception("Pipeline health check failed")

    # The board proper. Kept in its own try so that a missing Owner field
    # (i.e. scripts/add_ownership_fields.py has not been run yet) degrades to
    # the rest of the dashboard instead of blanking the page.
    board_error = None
    try:
        unclaimed = notion.unclaimed_items()
        mine = notion.items_owned_by(user)
    except Exception as e:
        logger.exception("Board queries failed")
        board_error = (
            "The board isn't set up yet — run scripts/add_ownership_fields.py "
            f"to add the Owner field. ({e})"
        )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "meetings": meetings,
            "no_future_meeting": not meetings and not error,
            "unclaimed": unclaimed,
            "mine": mine,
            "board_error": board_error,
            "health": health,
            "recent": recent,
            "drafts": drafts,
            "error": error,
        },
    )
