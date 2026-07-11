import logging

from fastapi import APIRouter, Depends, Form, Request

from app import mailer, notion
from app.auth import (
    create_reset_token,
    hash_password,
    require_user,
    verify_login,
    verify_reset_token,
)
from app.web import templates

logger = logging.getLogger(__name__)
router = APIRouter()

MIN_PASSWORD_LEN = 8


@router.get("/account")
async def account_page(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse(
        request, "account.html", {"user": user, "message": None, "error": None}
    )


@router.post("/account/password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: str = Depends(require_user),
):
    error = None
    if not verify_login(user, current_password):
        error = "Current password is wrong."
    elif new_password != confirm_password:
        error = "New passwords don't match."
    elif len(new_password) < MIN_PASSWORD_LEN:
        error = f"New password must be at least {MIN_PASSWORD_LEN} characters."
    if error:
        return templates.TemplateResponse(
            request, "account.html", {"user": user, "message": None, "error": error}
        )
    try:
        notion.set_portal_user_password(user, hash_password(new_password))
    except Exception as e:
        logger.exception("Password change failed")
        return templates.TemplateResponse(
            request,
            "account.html",
            {"user": user, "message": None, "error": f"Saving failed: {e}"},
        )
    return templates.TemplateResponse(
        request,
        "account.html",
        {"user": user, "message": "Password changed.", "error": None},
    )


@router.get("/forgot")
async def forgot_page(request: Request):
    return templates.TemplateResponse(request, "forgot.html", {"done": False})


@router.post("/forgot")
async def forgot_submit(request: Request, name: str = Form(...)):
    # Same response whether or not the user exists — don't leak the user list
    try:
        record = notion.get_portal_user(name)
        if record and record.get("email"):
            token = create_reset_token(record["name"])
            reset_url = str(request.base_url).rstrip("/") + f"/reset?token={token}"
            mailer.send_plain(
                record["email"],
                "Lambeth Cyclists portal — password reset",
                f"Hello {record['name']},\n\n"
                f"Someone (hopefully you) asked to reset your portal password.\n"
                f"This link works for 1 hour:\n\n{reset_url}\n\n"
                f"If it wasn't you, just ignore this email.",
            )
        else:
            logger.info("Password reset requested for unknown/no-email user %r", name)
    except Exception:
        logger.exception("Password reset flow failed")
    return templates.TemplateResponse(request, "forgot.html", {"done": True})


@router.get("/reset")
async def reset_page(request: Request, token: str = ""):
    user = verify_reset_token(token)
    return templates.TemplateResponse(
        request,
        "reset.html",
        {"token": token, "valid": bool(user), "error": None, "done": False},
    )


@router.post("/reset")
async def reset_submit(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = verify_reset_token(token)
    if not user:
        return templates.TemplateResponse(
            request,
            "reset.html",
            {"token": token, "valid": False, "error": None, "done": False},
        )
    error = None
    if new_password != confirm_password:
        error = "Passwords don't match."
    elif len(new_password) < MIN_PASSWORD_LEN:
        error = f"Password must be at least {MIN_PASSWORD_LEN} characters."
    if error:
        return templates.TemplateResponse(
            request,
            "reset.html",
            {"token": token, "valid": True, "error": error, "done": False},
        )
    notion.set_portal_user_password(user, hash_password(new_password))
    return templates.TemplateResponse(
        request,
        "reset.html",
        {"token": token, "valid": True, "error": None, "done": True},
    )
