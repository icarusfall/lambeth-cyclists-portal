"""On-demand AI actions (no daemons — every call here is a button press in the UI).

Model: claude-sonnet-5 with adaptive thinking (the default). Effort is kept at
low/medium to hold costs to pennies per newsletter.
"""

import logging
from datetime import date
from functools import lru_cache

import anthropic
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"


class Story(BaseModel):
    headline: str
    summary: str = Field(description="2-3 friendly sentences for a community newsletter")
    source: str = Field(description="Where this came from, e.g. 'Notion: <item title>' or a news site name")
    url: str | None = Field(default=None, description="Link for readers, if available")


class StoryList(BaseModel):
    stories: list[Story]


@lru_cache
def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key)


VOICE = (
    "Lambeth Cyclists is the Lambeth branch of the London Cycling Campaign, a "
    "friendly volunteer-run advocacy group in South London. The newsletter voice is "
    "warm, brief and practical — community noticeboard, not press release. "
    "Studiously apolitical: report on council/TfL plans factually."
)


def suggest_stories(items_md: str, projects_md: str) -> list[Story]:
    """Turn recent Notion items + active projects into candidate newsletter stories."""
    response = client().messages.parse(
        model=MODEL,
        max_tokens=4096,
        output_config={"effort": "low"},
        system=VOICE,
        messages=[
            {
                "role": "user",
                "content": (
                    "Below are recent items (mostly filed emails about consultations, "
                    "traffic orders and infrastructure) and active projects from our "
                    "Notion databases.\n\n"
                    f"## Recent items\n{items_md}\n\n## Active projects\n{projects_md}\n\n"
                    "Pick the 4-8 things a Lambeth cyclist would actually want to read "
                    "about this month and write each up as a newsletter story. Skip "
                    "admin noise, duplicates, and anything with no reader interest. "
                    "Mention consultation deadlines where they exist — encouraging "
                    "members to respond to consultations is a core purpose."
                ),
            }
        ],
        output_format=StoryList,
    )
    return response.parsed_output.stories


def news_scan(existing_headlines: list[str]) -> list[Story]:
    """Web-search for recent Lambeth cycling news not already covered by our items."""
    already = "\n".join(f"- {h}" for h in existing_headlines) or "(none)"
    response = client().messages.parse(
        model=MODEL,
        max_tokens=8000,
        output_config={"effort": "medium"},
        system=VOICE,
        tools=[
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": 6,
                "user_location": {
                    "type": "approximate",
                    "city": "London",
                    "region": "England",
                    "country": "GB",
                    "timezone": "Europe/London",
                },
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Today is {date.today().isoformat()}. Search for news from the "
                    "last ~6 weeks relevant to cycling in the London Borough of "
                    "Lambeth: infrastructure changes, road schemes, consultations, "
                    "events, incidents with policy relevance. Good sources include "
                    "Brixton Buzz, Lambeth Council news, London Cycling Campaign, "
                    "853, Southwark News, local press.\n\n"
                    "We already have stories on these, so skip anything covered:\n"
                    f"{already}\n\n"
                    "Return up to 5 genuinely new stories with the source URL. If "
                    "nothing new turns up, return an empty list — do not pad."
                ),
            }
        ],
        output_format=StoryList,
    )
    return response.parsed_output.stories


CHAT_SYSTEM = (
    VOICE
    + " You are the assistant inside the Lambeth Cyclists members' portal, "
    "talking to a committee member. Use the Notion tools to look things up "
    "before answering — never guess or make up data. You can summarise "
    "anything in the databases (all filed emails/items, meetings, projects, "
    "ward and councillor research), including things that weren't picked for "
    "the newsletter. Keep answers concise and practical; members are busy. "
    "You have read-only access — for edits, point them at Notion or the "
    "newsletter builder. If you can't find something, say so honestly."
)


def chat_reply(messages: list[dict]) -> str:
    """One portal-chat turn. `messages` is the full [{role, content}] history.

    Uses the MCP connector to give Claude the CycleBot MCP server's read-only
    Notion tools. Server-side tool loops can pause (`pause_turn`) — resume a
    few times before giving up.
    """
    settings = get_settings()
    mcp_servers = [
        {
            "type": "url",
            "url": settings.mcp_server_url,
            "name": "lambeth-cyclists",
            "authorization_token": settings.mcp_api_key,
        }
    ]
    convo = list(messages)
    for _ in range(4):
        response = client().with_options(timeout=120.0).beta.messages.create(
            model=MODEL,
            max_tokens=4096,
            output_config={"effort": "medium"},
            system=CHAT_SYSTEM,
            betas=["mcp-client-2025-11-20"],
            mcp_servers=mcp_servers,
            tools=[{"type": "mcp_toolset", "mcp_server_name": "lambeth-cyclists"}],
            messages=convo,
        )
        if response.stop_reason != "pause_turn":
            break
        convo = convo + [{"role": "assistant", "content": response.content}]
    return "".join(b.text for b in response.content if b.type == "text").strip()


def draft_newsletter(
    stories_md: str, meetings_md: str, month_label: str
) -> str:
    """Draft the full newsletter in markdown from the selected stories + meetings."""
    response = client().messages.create(
        model=MODEL,
        max_tokens=4096,
        output_config={"effort": "medium"},
        system=VOICE,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Draft our {month_label} newsletter in markdown.\n\n"
                    f"## Selected stories (use all of these, edited text is final-ish)\n"
                    f"{stories_md}\n\n"
                    f"## Upcoming meetings (always include a 'Get involved' section "
                    f"with these)\n{meetings_md}\n\n"
                    "Structure: a one-paragraph friendly intro, the stories with "
                    "short ## headings, then 'Get involved' with meeting details and "
                    "a line inviting people to reply if they'd like to help out. "
                    "Keep the whole thing readable in under 3 minutes on a phone. "
                    "Include story links where given. Do not invent facts beyond "
                    "what's provided. Return ONLY the newsletter markdown — no "
                    "preamble or commentary."
                ),
            }
        ],
    )
    return next(b.text for b in response.content if b.type == "text").strip()
