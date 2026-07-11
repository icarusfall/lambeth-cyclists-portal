# Lambeth Cyclists Portal

Phone-friendly, login-protected portal at **lambethcyclists.com** that makes
producing the monthly Lambeth Cyclists newsletter nearly effortless.

- **Dashboard** — next committee meeting, a warning banner when no future
  meeting is diarised, approaching consultation deadlines, recent items.
- **Newsletter builder** — one page, three steps:
  1. *Gather*: AI-suggested stories from the Notion Items/Projects databases,
     plus an on-demand web news scan, plus manual stories.
  2. *Draft*: AI drafts the newsletter in markdown; edit, preview, save to Notion.
  3. *Send*: test send to yourself, then send to the Google Group (Resend)
     and/or produce copy-paste HTML + plain text for the LCC messaging system.
- **Archive** — every draft and sent newsletter, stored in a Notion database.

## Stack

FastAPI + Jinja2 + htmx. Notion is the only datastore. AI actions are strictly
on-demand (button presses) using `claude-sonnet-5` — no background jobs.

## Local development

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # then fill it in
python scripts/hash_password.py charlie   # generate PORTAL_USERS entries
python scripts/create_newsletters_db.py <notion_parent_page_id>
uvicorn app.main:app --reload
```

## Deployment

Railway service linked to this repo; `railway.json` sets the start command.
Set every variable from `.env.example` in the Railway environment. Custom
domain: add `lambethcyclists.com` in Railway → Settings → Domains and create
the DNS record it shows at your registrar. Verify the domain in Resend so
`NEWSLETTER_FROM` can send, and add that address as an allowed poster on the
Google Group.

## Environment variables

See [.env.example](.env.example) — session secret, users (bcrypt hashes),
Notion token + database IDs, Anthropic key, Resend key, group address.
