import logging

from fastapi import APIRouter, Depends, Request

from app import notion
from app.auth import require_user
from app.web import render_markdown, templates

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/archive")
async def archive_list(request: Request, user: str = Depends(require_user)):
    error = None
    drafts, sent = [], []
    try:
        for nl in notion.list_newsletters():
            if nl["props"].get("Status") == notion.NEWSLETTER_STATUS_SENT:
                sent.append(nl)
            else:
                drafts.append(nl)
    except Exception as e:
        logger.exception("Archive list failed")
        error = str(e)
    return templates.TemplateResponse(
        request,
        "archive.html",
        {"user": user, "drafts": drafts, "sent": sent, "error": error},
    )


@router.get("/archive/{page_id}")
async def archive_view(
    request: Request, page_id: str, user: str = Depends(require_user)
):
    nl = notion.load_newsletter(page_id)
    return templates.TemplateResponse(
        request,
        "archive_view.html",
        {"user": user, "nl": nl, "html_body": render_markdown(nl["markdown"])},
    )
