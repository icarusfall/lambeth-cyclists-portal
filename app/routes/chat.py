import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import ai
from app.auth import require_user
from app.config import get_settings
from app.web import render_markdown, templates

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_TURNS = 20  # cap history sent to the API to keep token costs bounded


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@router.get("/chat")
async def chat_page(request: Request, user: str = Depends(require_user)):
    return templates.TemplateResponse(
        request,
        "chat.html",
        {"user": user, "configured": bool(get_settings().mcp_api_key)},
    )


@router.post("/chat/api")
async def chat_api(
    body: ChatRequest, user: str = Depends(require_user)
):
    if not get_settings().mcp_api_key:
        return JSONResponse(
            {"error": "Chat isn't configured yet (MCP_API_KEY missing)."},
            status_code=503,
        )
    messages = [
        {"role": m.role, "content": m.content}
        for m in body.messages[-MAX_TURNS:]
        if m.role in ("user", "assistant") and m.content.strip()
    ]
    if not messages or messages[-1]["role"] != "user":
        return JSONResponse({"error": "No message to answer."}, status_code=400)
    try:
        reply = ai.chat_reply(messages)
    except Exception as e:
        logger.exception("Chat failed")
        return JSONResponse({"error": f"Chat failed: {e}"}, status_code=502)
    return JSONResponse({"markdown": reply, "html": render_markdown(reply)})
