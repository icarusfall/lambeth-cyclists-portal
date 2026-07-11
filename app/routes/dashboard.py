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
    meetings, deadlines, recent, drafts = [], [], [], []
    try:
        meetings = notion.upcoming_meetings()
        deadlines = notion.items_with_deadlines()
        recent = notion.recent_items(days=30)
        drafts = notion.current_drafts()
    except Exception as e:
        logger.exception("Dashboard Notion queries failed")
        error = f"Couldn't load data from Notion: {e}"

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "meetings": meetings,
            "no_future_meeting": not meetings and not error,
            "deadlines": deadlines,
            "recent": recent,
            "drafts": drafts,
            "error": error,
        },
    )
