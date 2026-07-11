"""Shared Jinja2 environment + small template helpers."""

import markdown as md
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


def render_markdown(text: str) -> str:
    return md.markdown(text or "", extensions=["extra"])


templates.env.filters["markdown"] = render_markdown
