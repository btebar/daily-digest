# Daily Digest

A daily email of the most notable papers, articles, and releases on **your**
topics. It collects from arXiv + RSS feeds, has Claude curate and summarise the
best of it, and emails you a digest every morning — all on free infrastructure
(GitHub Actions + Resend).

Each morning you get:

- **Top picks, explained** — the ~5 most important items, each with a clear
  teaching paragraph you can actually learn from.
- **Also worth a look** — the rest, grouped by theme with a one-line why.

## How it works

```
GitHub Actions (daily cron)
   └─ python -m digest.main
        ├─ collect.py  → pull recent items from arXiv API + RSS feeds
        ├─ curate.py   → Claude picks + summarises the notable ones
        ├─ render.py   → build the HTML email
        └─ send.py     → deliver via Resend → your inbox
```

Everything is driven by **`config.yaml`** — topics, sources, model, send time.

## Setup (one time, ~10 minutes)

### 1. Get the two API keys

- **Gemini** (curation): https://aistudio.google.com/apikey → create an API
  key (free tier is generous and covers daily use).
- **Resend** (email): https://resend.com → sign up → API Keys → create one.
  - To send *from your own domain*, add + verify it under Resend → Domains,
    then set `email.from` in `config.yaml` to e.g. `digest@yourdomain.com`.
  - No domain yet? Set `email.from: "onboarding@resend.dev"`. That test sender
    can only deliver to the email you signed up to Resend with — fine to start.

### 2. Push this repo to GitHub

```bash
cd daily-digest
git init && git add . && git commit -m "Daily digest"
gh repo create daily-digest --private --source=. --push
```

### 3. Add the keys as repo secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

- `GEMINI_API_KEY`
- `RESEND_API_KEY`
- `REDDIT_CLIENT_ID` *(optional — enables Reddit; see below)*
- `REDDIT_CLIENT_SECRET` *(optional)*

That's it — the workflow runs itself every morning at **7 AM UK time**.

### Optional: enable Reddit (official API)

Reddit as a source needs a free API app (reliable, not rate-limited like the
public RSS):

1. Go to https://www.reddit.com/prefs/apps → **Create another app…**
2. Choose type **script**, give it any name, set redirect URI to
   `http://localhost` (unused), and create it.
3. Copy the **client ID** (the string under the app name) and the **secret**.
4. Add them as repo secrets `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`
   (or `export` them locally for `--dry-run`).

Edit the subreddit list under `sources.reddit` in `config.yaml`. Skip this step
entirely and Reddit is just left out.

## Test it now

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Preview without sending — writes digest.html and prints a text version.
export GEMINI_API_KEY=...
python -m digest.main --dry-run
open digest.html

# Not sure which model name to use? List what your key supports:
python -m digest.list_models

# Full run including the email (needs RESEND_API_KEY too):
export RESEND_API_KEY=re_...
python -m digest.main --force
```

Or trigger the real workflow from GitHub: **Actions → Daily Digest → Run workflow**.

## Customising

Everything lives in `config.yaml`:

| Setting | What it does |
| --- | --- |
| `topics` | Your interests, in plain words. The model reads these verbatim. |
| `sources.arxiv.categories` | Which arXiv categories to pull (e.g. `cs.AI`, `cs.LG`). |
| `sources.rss` | Any RSS/Atom feed — news, blogs, newsletters, GitHub releases (`…/releases.atom`). |
| `curation.model` | `gemini-flash-latest` (default) or `gemini-pro-latest` for sharper picks. Run `python -m digest.list_models` to see valid names. |
| `curation.max_items` / `lookback_hours` | Digest length and freshness window. |
| `send_time` | Hour + timezone. Delivery is DST-safe (see below). |

### Send time & DST

GitHub Actions cron only runs in UTC and doesn't shift for BST/GMT. The workflow
fires at both 06:00 and 07:00 UTC; `main.should_run_now()` checks the real local
time in your configured timezone and only proceeds at the target hour. To change
the time, edit `send_time.hour` in `config.yaml` **and** the two `cron:` lines in
`.github/workflows/digest.yml` to bracket it.

## Cost

Curation is one Gemini call per day over ~100 candidates. Gemini's free tier
comfortably covers a daily Flash call, and GitHub Actions + Resend free tiers
cover the rest — so this typically runs at no cost.
