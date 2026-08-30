"""Sorting filed items into projects.

Items are leads; projects are the work. This is the step that carries one
across to the other, and it is deliberately a five-minute job: Claude
proposes the groupings, a person accepts or rejects each one.

Nothing is written to Notion until somebody presses a button.
"""

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import ai, notion
from app.auth import require_user
from app.web import templates

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/triage")
async def triage_page(request: Request, user: str = Depends(require_user)):
    error = None
    items, projects, standing = [], [], []
    try:
        items = notion.untriaged_items()
        all_projects = notion.projects_for_matching()
        projects = [p["title"] for p in all_projects]
        standing = [p["title"] for p in all_projects if p["standing"]]
    except Exception as e:
        logger.exception("Triage queue failed to load")
        error = f"Couldn't load the queue from Notion: {e}"
    return templates.TemplateResponse(
        request,
        "triage.html",
        {"user": user, "items": items, "projects": projects,
         "standing": standing, "error": error},
    )


@router.post("/triage/propose")
async def propose(request: Request, user: str = Depends(require_user)):
    """Read the whole untriaged queue and propose project groupings."""
    try:
        items = notion.untriaged_items()
        if not items:
            return templates.TemplateResponse(
                request, "partials/_error.html", {"error": "Nothing left to sort."}
            )
        result = ai.propose_projects(items, notion.projects_for_matching())
    except Exception as e:
        logger.exception("Triage proposal failed")
        return templates.TemplateResponse(
            request, "partials/_error.html", {"error": f"Couldn't sort those: {e}"}
        )

    # Map the model's item numbers back to real pages, ignoring any it invented.
    by_number = {i["number"]: i for i in items}
    proposals = []
    for n, p in enumerate(result.proposals):
        members = [by_number[num] for num in p.item_numbers if num in by_number]
        if members:
            proposals.append({"index": n, "p": p, "items": members})
    dropped = [by_number[num] for num in result.not_relevant if num in by_number]

    logger.info(
        "Triage proposed %d project(s) over %d item(s); %d marked not relevant",
        len(proposals), len(items), len(dropped),
    )
    # Sweeps into projects we already run are the cheap, encouraged path;
    # brand-new projects are shown separately and need a deliberate press.
    sweeps = [p for p in proposals if p["p"].matches_existing]
    new = [p for p in proposals if not p["p"].matches_existing]
    return templates.TemplateResponse(
        request,
        "partials/_triage_proposals.html",
        {
            "sweeps": sweeps,
            "new": new,
            "dropped": dropped,
            "reasoning": result.reasoning,
            "all_projects": [p["title"] for p in notion.projects_for_matching()],
        },
    )


@router.post("/triage/accept")
async def accept(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    project_type: str = Form("infrastructure_campaign"),
    geographic_scope: str = Form("neighbourhood"),
    priority: str = Form("medium"),
    primary_locations: str = Form(""),
    next_action: str = Form(""),
    matches_existing: str = Form(""),
    item_ids: str = Form(""),
    user: str = Depends(require_user),
):
    """Create (or reuse) the project and attach its items."""
    ids = [i for i in item_ids.split(",") if i.strip()]
    try:
        project_id = notion.find_project_by_title(matches_existing) if matches_existing else None
        if project_id:
            url, verb = None, "added to"
        else:
            created = notion.create_project(
                title=title.strip(),
                description=description.strip(),
                project_type=project_type,
                geographic_scope=geographic_scope,
                priority=priority,
                primary_locations=[l.strip() for l in primary_locations.split(",") if l.strip()],
                next_action=next_action.strip(),
            )
            project_id, url, verb = created["id"], created["url"], "created"
        attached = notion.attach_items_to_project(ids, project_id)
    except Exception as e:
        logger.exception("Accepting a triage proposal failed")
        return templates.TemplateResponse(
            request, "partials/_error.html", {"error": f"Couldn't save that: {e}"}
        )

    logger.info("%s %s project %r with %d item(s)", user, verb, title.strip()[:60], attached)
    return templates.TemplateResponse(
        request,
        "partials/_triage_done.html",
        {"title": title.strip(), "attached": attached, "verb": verb, "url": url},
    )


@router.post("/triage/dismiss")
async def dismiss(request: Request, item_ids: str = Form(""), user: str = Depends(require_user)):
    """Mark items as needing no project at all."""
    ids = [i for i in item_ids.split(",") if i.strip()]
    try:
        closed = notion.set_items_not_relevant(ids)
    except Exception as e:
        logger.exception("Dismissing items failed")
        return templates.TemplateResponse(
            request, "partials/_error.html", {"error": f"Couldn't do that: {e}"}
        )
    logger.info("%s closed %d item(s) as not relevant", user, closed)
    return templates.TemplateResponse(
        request, "partials/_triage_done.html",
        {"title": f"{closed} item{'s' if closed != 1 else ''}", "attached": 0,
         "verb": "filed away", "url": None},
    )
