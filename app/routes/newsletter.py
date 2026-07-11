import logging
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from app import ai, mailer, notion
from app.auth import require_user
from app.config import get_settings
from app.web import render_markdown, templates

logger = logging.getLogger(__name__)
router = APIRouter()


def month_label() -> str:
    return date.today().strftime("%B %Y")


def pages_to_md(pages: list[dict]) -> str:
    """Simplified Notion pages -> compact markdown for AI prompts."""
    parts = []
    for p in pages:
        lines = [f"### {p['title']}"]
        for name, value in p["props"].items():
            lines.append(f"- {name}: {value}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts) or "(nothing)"


async def parse_story_form(request: Request) -> list[dict]:
    """Read story cards from the builder form. Cards use suffixed field names
    (headline_0, summary_0, include_0, ...); only cards with their include
    checkbox ticked come back."""
    form = await request.form()
    indices = sorted(
        {
            key.split("_")[-1]
            for key in form.keys()
            if key.startswith("headline_")
        },
        key=lambda s: int(s) if s.isdigit() else 0,
    )
    stories = []
    for i in indices:
        if not form.get(f"include_{i}"):
            continue
        headline = (form.get(f"headline_{i}") or "").strip()
        summary = (form.get(f"summary_{i}") or "").strip()
        if not headline and not summary:
            continue
        stories.append(
            {
                "headline": headline,
                "summary": summary,
                "source": (form.get(f"source_{i}") or "").strip(),
                "url": (form.get(f"url_{i}") or "").strip(),
            }
        )
    return stories


def stories_to_md(stories: list[dict]) -> str:
    parts = []
    for s in stories:
        lines = [f"### {s['headline']}", s["summary"]]
        if s.get("url"):
            lines.append(f"Link: {s['url']}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts) or "(no stories selected)"


# ---------------------------------------------------------------------------
# Builder page
# ---------------------------------------------------------------------------


@router.get("/newsletter")
async def builder(
    request: Request, id: str | None = None, user: str = Depends(require_user)
):
    existing = None
    if id:
        try:
            existing = notion.load_newsletter(id)
        except Exception as e:
            logger.exception("Failed to load newsletter %s", id)
            existing = {"error": str(e)}
    return templates.TemplateResponse(
        request,
        "newsletter.html",
        {
            "user": user,
            "month": month_label(),
            "existing": existing,
            "group_email": get_settings().group_email,
        },
    )


# ---------------------------------------------------------------------------
# Gather (htmx partials)
# ---------------------------------------------------------------------------


@router.post("/newsletter/suggest")
async def suggest(request: Request, user: str = Depends(require_user)):
    try:
        items_md = pages_to_md(notion.recent_items(days=45))
        projects_md = pages_to_md(notion.active_projects())
        stories = ai.suggest_stories(items_md, projects_md)
    except Exception as e:
        logger.exception("Suggest stories failed")
        return templates.TemplateResponse(
            request, "partials/_error.html", {"message": f"Suggesting stories failed: {e}"}
        )
    return templates.TemplateResponse(
        request,
        "partials/_stories.html",
        {"stories": [s.model_dump() for s in stories], "label": "From Notion"},
    )


@router.post("/newsletter/news-scan")
async def scan_news(request: Request, user: str = Depends(require_user)):
    form = await request.form()
    existing_headlines = [
        str(v).strip() for k, v in form.multi_items() if k.startswith("headline_") and str(v).strip()
    ]
    try:
        stories = ai.news_scan(existing_headlines)
    except Exception as e:
        logger.exception("News scan failed")
        return templates.TemplateResponse(
            request, "partials/_error.html", {"message": f"News scan failed: {e}"}
        )
    if not stories:
        return templates.TemplateResponse(
            request,
            "partials/_error.html",
            {"message": "News scan found nothing new — no extra stories added."},
        )
    return templates.TemplateResponse(
        request,
        "partials/_stories.html",
        {"stories": [s.model_dump() for s in stories], "label": "From the news"},
    )


# ---------------------------------------------------------------------------
# Draft
# ---------------------------------------------------------------------------


@router.post("/newsletter/draft")
async def draft(request: Request, user: str = Depends(require_user)):
    form = await request.form()
    stories = await parse_story_form(request)
    if not stories:
        return templates.TemplateResponse(
            request,
            "partials/_error.html",
            {"message": "Tick at least one story before drafting."},
        )
    try:
        meetings_md = pages_to_md(notion.upcoming_meetings())
        markdown_body = ai.draft_newsletter(
            stories_to_md(stories), meetings_md, month_label()
        )
    except Exception as e:
        logger.exception("Draft newsletter failed")
        return templates.TemplateResponse(
            request, "partials/_error.html", {"message": f"Drafting failed: {e}"}
        )
    return templates.TemplateResponse(
        request,
        "partials/_draft.html",
        {
            "markdown_body": markdown_body,
            "subject": form.get("subject") or f"Lambeth Cyclists — {month_label()}",
            "page_id": form.get("page_id") or "",
            "saved": False,
        },
    )


@router.post("/newsletter/preview")
async def preview(
    request: Request,
    markdown_body: str = Form(""),
    user: str = Depends(require_user),
):
    return Response(
        f'<div class="preview card">{render_markdown(markdown_body)}</div>',
        media_type="text/html",
    )


@router.post("/newsletter/save")
async def save(
    request: Request,
    markdown_body: str = Form(...),
    subject: str = Form(...),
    page_id: str = Form(""),
    user: str = Depends(require_user),
):
    try:
        saved_id = notion.save_newsletter_draft(
            title=f"Newsletter — {month_label()}",
            subject=subject,
            markdown_body=markdown_body,
            page_id=page_id or None,
        )
    except Exception as e:
        logger.exception("Save draft failed")
        return templates.TemplateResponse(
            request, "partials/_error.html", {"message": f"Saving to Notion failed: {e}"}
        )
    return templates.TemplateResponse(
        request,
        "partials/_draft.html",
        {
            "markdown_body": markdown_body,
            "subject": subject,
            "page_id": saved_id,
            "saved": True,
        },
    )


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


@router.get("/newsletter/{page_id}/send")
async def send_page(
    request: Request, page_id: str, user: str = Depends(require_user)
):
    nl = notion.load_newsletter(page_id)
    return templates.TemplateResponse(
        request,
        "send.html",
        {
            "user": user,
            "nl": nl,
            "html_preview": render_markdown(nl["markdown"]),
            "group_email": get_settings().group_email,
        },
    )


@router.post("/newsletter/{page_id}/send-test")
async def send_test(
    request: Request,
    page_id: str,
    test_email: str = Form(...),
    user: str = Depends(require_user),
):
    nl = notion.load_newsletter(page_id)
    try:
        mailer.send_newsletter(
            f"[TEST] {nl['subject']}", nl["markdown"], test_email.strip()
        )
    except Exception as e:
        logger.exception("Test send failed")
        return templates.TemplateResponse(
            request, "partials/_error.html", {"message": f"Test send failed: {e}"}
        )
    return Response(
        f'<p class="flash ok">Test sent to {test_email} — check how it looks on your phone.</p>',
        media_type="text/html",
    )


@router.post("/newsletter/{page_id}/send")
async def send(
    request: Request,
    page_id: str,
    user: str = Depends(require_user),
):
    form = await request.form()
    to_group = bool(form.get("channel_group"))
    for_lcc = bool(form.get("channel_lcc"))
    if not (to_group or for_lcc):
        return templates.TemplateResponse(
            request, "partials/_error.html", {"message": "Pick at least one channel."}
        )

    nl = notion.load_newsletter(page_id)
    channels = []
    group_result = None

    if to_group:
        group_email = get_settings().group_email
        if not group_email:
            return templates.TemplateResponse(
                request,
                "partials/_error.html",
                {"message": "GROUP_EMAIL isn't configured — can't send to the group."},
            )
        try:
            group_result = mailer.send_newsletter(
                nl["subject"], nl["markdown"], group_email
            )
            channels.append("Google Group")
        except Exception as e:
            logger.exception("Group send failed")
            return templates.TemplateResponse(
                request,
                "partials/_error.html",
                {"message": f"Sending to the group failed (nothing marked as sent): {e}"},
            )

    if for_lcc:
        channels.append("LCC")

    try:
        notion.mark_newsletter_sent(page_id, sent_by=user, channels=channels)
    except Exception as e:
        logger.exception("Marking sent failed")
        # The email (if any) already went — surface but don't pretend it failed
        return templates.TemplateResponse(
            request,
            "partials/_error.html",
            {
                "message": (
                    f"Email was sent, but updating Notion failed: {e}. "
                    "Set the status manually in Notion."
                )
            },
        )

    return templates.TemplateResponse(
        request,
        "partials/_sent.html",
        {
            "nl": nl,
            "channels": channels,
            "group_result": group_result,
            "for_lcc": for_lcc,
            "html_body": mailer.markdown_to_email_html(nl["markdown"]),
            "text_body": nl["markdown"],
        },
    )
