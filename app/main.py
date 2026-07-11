"""Lambeth Cyclists portal — FastAPI app entry point."""

import logging

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    LoginRequired,
    create_session_token,
    verify_login,
)
from app.routes import archive, dashboard, newsletter
from app.web import templates

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Lambeth Cyclists Portal")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired):
    # htmx requests can't follow a normal redirect usefully — tell htmx to do it
    if request.headers.get("HX-Request"):
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login_submit(
    request: Request, name: str = Form(...), password: str = Form(...)
):
    user = verify_login(name, password)
    if not user:
        return templates.TemplateResponse(
            request, "login.html", {"error": "Wrong name or password."}, status_code=401
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(dashboard.router)
app.include_router(newsletter.router)
app.include_router(archive.router)
