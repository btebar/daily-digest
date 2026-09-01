# Weekly Digest

A weekly email of the most notable papers, articles, and releases on **your**
topics. It collects from arXiv + RSS feeds (and optionally Reddit), has Gemini
curate and summarise the best of it, and emails you a digest every Monday
morning — all on free infrastructure (GitHub Actions + Resend).

Each Monday you get:

- **Top picks, explained** — the ~5 most important items, each with a clear
  teaching paragraph you can actually learn from.
- **Also worth a look** — the rest, grouped by theme with a one-line why.

## How it works

```
GitHub Actions (weekly cron — Monday)
   └─ python -m digest.main
        ├─ collect.py  → pull recent items from arXiv API + RSS feeds
        ├─ curate.py   → Gemini picks + summarises the notable ones
        ├─ render.py   → build the HTML email
        └─ send.py     → deliver via Resend → your inbox
```

Everything is driven by **`config.yaml`** — topics, sources, model, send time.

## Setup (one time, ~10 minutes)

### 1. Get the two API keys

- **Gemini** (curation): https://aistudio.google.com/apikey → create an API
  key (the free tier comfortably covers one call a week).
- **Resend** (email): https://resend.com → sign up → API Keys → create one.
  - To send *from your own domain*, add + verify it under Resend → Domains,
    then set `email.from` in `config.yaml` to e.g. `digest@yourdomain.com`.
  - No domain yet? Set `email.from: "onboarding@resend.dev"`. That test sender
    can only deliver to the email you signed up to Resend with — fine to start.

### 2. Clone and make it yours

```bash
git clone https://github.com/<you>/daily-digest.git
cd daily-digest
cp config.example.yaml config.yaml   # your personal config (gitignored)
```

Open **`config.yaml`** and set at least:

- `email.to` — where the digest is delivered (**required** — the app refuses to
  send while this is the `you@example.com` placeholder).
- `email.from` — your verified Resend sender, or `onboarding@resend.dev` to start.
- `topics` — your interests, in plain words.

`config.yaml` is **gitignored**, so your topics and email never get committed —
which means the repo is safe to make public, and a clone only ever ships the
neutral `config.example.yaml`.

Then push it to **your own** GitHub repo:

```bash
gh repo create daily-digest --private --source=. --push
```

### 3. Add your keys and config to the repo

Repo → **Settings → Secrets and variables → Actions**.

Under **Secrets** → *New repository secret*:

- `GEMINI_API_KEY`
- `RESEND_API_KEY`
- `REDDIT_CLIENT_ID` *(optional — enables Reddit; see below)*
- `REDDIT_CLIENT_SECRET` *(optional)*

Under **Variables** → *New repository variable*:

- `CONFIG_YAML` — paste the **entire contents of your `config.yaml`**. Because
  `config.yaml` isn't committed, the workflow writes this variable back to
  `config.yaml` at runtime. (A *variable*, not a secret: config isn't sensitive,
  and secrets get masked in logs, which would garble matching text.) Update this
  variable whenever you change your topics or sources.

That's it — the workflow runs itself every **Monday at 7 AM UK time**.

To change the day, edit the `cron:` lines in `.github/workflows/digest.yml`
(`* * 1` is Monday, `* * 5` is Friday, and so on) and move the Tuesday
catch-up line to the day after. To go back to daily, set
`send_time.min_interval_days: 0` and use `* * *`.

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
cp config.example.yaml config.yaml   # then edit email.to + topics

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

Or trigger the real workflow from GitHub: **Actions → Weekly Digest → Run
workflow**. A manual run uses `--force`, so it sends immediately and does *not*
consume the weekly slot.

## Development

Install the dev tooling and run the checks (ruff, mypy, pytest) — the same ones
CI runs on every push (`.github/workflows/ci.yml`):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check .          # lint
ruff format --check . # formatting
mypy digest           # type check
pytest -q             # tests
```

Runtime dependency versions are pinned in `requirements.txt` (the lock CI
installs from); the abstract ranges live in `pyproject.toml`. After upgrading a
dependency, refresh the lock with `pip freeze > requirements.txt`.

## Customising

Everything lives in `config.yaml`:

| Setting | What it does |
| --- | --- |
| `topics` | Your interests, in plain words. The model reads these verbatim. |
| `sources.arxiv.categories` | Which arXiv categories to pull (e.g. `cs.AI`, `cs.LG`). |
| `sources.rss` | Any RSS/Atom feed — news, blogs, newsletters, GitHub releases (`…/releases.atom`). |
| `curation.model` | `gemini-flash-latest` (default) or `gemini-pro-latest` for sharper picks. Run `python -m digest.list_models` to see valid names. |
| `curation.max_items` / `lookback_hours` | Digest length and freshness window. |
| `curation.fallback_model` | Second model tried if the first is overloaded (see below). |
| `send_time` | Hour, timezone, and `min_interval_days`. Delivery is DST-safe (see below). |

### Schedule & DST

GitHub Actions cron only runs in UTC and doesn't shift for BST/GMT, and it
drops or delays scheduled runs under load. So the workflow fires **three times
on Monday morning** (06:00, 07:00, 08:00 UTC) plus a **Tuesday catch-up**.
`main.send_gate()` checks the real local time in your timezone and sends on the
first fire that is both at/after `send_time.hour` and at least
`send_time.min_interval_days` since the last send; every later fire no-ops.

That means the *cadence* comes from two places working together:

- the **cron day** in `.github/workflows/digest.yml` decides which morning,
- **`min_interval_days: 6`** guarantees you never get two digests in a week,
  even if GitHub fires something unexpected.

The interval is 6 rather than 7 so that if *every* Monday slot gets dropped,
Tuesday's run still rescues that week's digest. Normally Tuesday just no-ops.

### If a run fails

Curation is a single Gemini call, and Gemini returns `503 UNAVAILABLE` ("model
is currently experiencing high demand") fairly often at busy times. `curate.py`
retries a transient error (429/5xx) three times with a growing backoff
(20s → 60s → 150s), then falls back to `curation.fallback_model` if one is
configured — overload is per-model, so a different one usually gets through.
Permanent errors (e.g. a bad request) fail fast rather than sitting through the
backoff.

If the run still fails, you get **one** alert email — a marker in
`state/last_alert` stops the later fires of the same morning from each sending
their own copy. Those later fires do still retry the digest itself.

### arXiv coverage

`sources.arxiv.max_results` fetches the **newest N** papers in your categories,
not "everything since the cutoff". 2000 (the arXiv API's per-request maximum)
reached back about **6 days** at the volume these categories ran at in Aug 2026,
which covers most of a week. arXiv volume grows over time, so this window
shrinks — if you start noticing the early part of the week going missing, that's
why, and covering a full week would need paginated fetching plus a cheap
pre-filter before the curation call.

Note also that arXiv only announces Sun–Thu, so a Monday digest naturally covers
the previous Sun–Thu papers plus whatever the RSS feeds published over the
weekend.

### Run state

`state/` holds the send marker, the failure-alert marker, and the
`seen_urls.json` dedup cache. It's **not committed** — the workflow persists it
between runs via the
[Actions cache](https://docs.github.com/actions/using-workflows/caching-dependencies-to-speed-up-workflows).
This keeps `main` free of `chore:` commits and avoids push races between the
morning's cron fires.

GitHub evicts caches unused for ~7 days, which a weekly schedule sits right on
the edge of — the Tuesday catch-up run helps by touching the cache a second time
each week. If it's evicted anyway, nothing breaks: the send marker's absence just
means the next Monday sends normally, and the dedup memory starts fresh (at worst
you see a few recently-sent items again). Locally, `state/` is created on first
run.

## Cost

Curation is one Gemini call per week over ~2000 candidates — roughly 300k input
tokens, about **$0.09 a week** at Flash pricing, and often $0 since it may fit
inside Gemini's free tier. GitHub Actions + Resend free tiers cover the rest.
To cut it further, lower `sources.arxiv.max_results`.
