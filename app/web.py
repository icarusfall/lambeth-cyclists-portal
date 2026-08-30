"""Shared Jinja2 environment + small template helpers."""

from datetime import date

import markdown as md
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


def render_markdown(text: str) -> str:
    return md.markdown(text or "", extensions=["extra"])


_MONTHS = (
    "January February March April May June July "
    "August September October November December"
).split()


def humandate(value) -> str:
    """'9 September' — no platform-specific strftime directives.

    %-d is glibc-only and %#d is Windows-only, so neither survives the trip
    between local development and Railway. This does the job in both.
    """
    if not value:
        return ""
    try:
        return f"{value.day} {_MONTHS[value.month - 1]}"
    except (AttributeError, IndexError):
        return str(value)


templates.env.filters["markdown"] = render_markdown
templates.env.filters["humandate"] = humandate

# Available in every template, including the standalone htmx partials, so
# deadline countdowns don't depend on each route remembering to pass it.
# Bound as the function, not a value: the server is long-running, so a date
# evaluated at import would freeze at deploy time. Templates call today().
templates.env.globals["today"] = date.today
