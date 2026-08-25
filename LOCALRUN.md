# Local command-line apps

Every runnable entry point in this repo, run from `src/` with
`poetry run python -m <module>` unless noted otherwise. All of them read
`src/services/.env` (see `INSTALLATION.md`'s "Database" section for first-time
setup) — nothing here
touches Cloud Run/Pub/Sub directly, so this is what you run against a local
or dev database before anything is deployed.

| Command | What it's for |
|---|---|
| `services.admin.seed_organisers` | Load `admin/data/organisers_seed.csv` into the `organisers` table |
| `services.admin.discover_sitemaps` | Fill in the seed CSV's `sitemap_url` column from each organiser's robots.txt |
| `services.local.local_event_scraper` | Run the pattern-website crawl pipeline (listing → event pages) in-process |
| `services.local.local_feed_importer` | Run one structured-feed importer (parkrun, ...) in-process |
| `services.server.main:app` | The crawler's Pub/Sub push-endpoint HTTP server (production entrypoint, run locally via `uvicorn`) |
| `services.admin.web.app:app` | The admin web interface (browsable DB view, run locally via `uvicorn`) |

## `services.admin.seed_organisers`

Loads the hand-maintained organiser list (`admin/data/organisers_seed.csv`)
into the `organisers` table. Phase-1 organiser sourcing — there's no
automated aggregator discovery yet (see `DIFFERENCES.md` F4), so this CSV is
the only way new organisers get in. Safe to re-run: matches existing rows by
`homepage_url` rather than re-inserting, and re-syncs `sitemap_url`/
`registrator` on an already-existing row if the CSV changed either since the
last run.

```
python -m services.admin.seed_organisers                  # seed only
python -m services.admin.seed_organisers --publish         # seed + enqueue a listing-crawl for every organiser
```

`--publish` needs `GCP_PROJECT_ID` set and the `listing-crawl` Pub/Sub topic
to actually exist (see `INSTALLATION.md`) — without it, use
`local_event_scraper` below instead, which crawls in-process with no Pub/Sub
involved at all.

## `services.admin.discover_sitemaps`

Maintenance script: visits every organiser's `robots.txt` in the seed CSV and
records whatever `Sitemap:` entry it advertises, so the crawl pipeline can
read that sitemap directly instead of paging through the listing page. Reads
and rewrites `admin/data/organisers_seed.csv` in place — run
`seed_organisers` again afterwards to get the new `sitemap_url` into the
database (or just re-run it any time; it's what already syncs `sitemap_url`
for existing rows).

```
python -m services.admin.discover_sitemaps
```

No arguments. Hits ~170 different sites in one run (one `GET .../robots.txt`
each, with retries/backoff), so it takes a few minutes.

## `services.local.local_event_scraper`

The pattern-website pipeline (organiser homepage → listing page → event
page), run in-process against your local/dev database — no Pub/Sub, no Cloud
Run. This is what `server/main.py`'s `/tasks/listing-crawl` and
`/tasks/event-crawl` handlers run in production instead; use this script for
local development/debugging.

```
python -m services.local.local_event_scraper --limit 3                       # only the first 3 organisers
python -m services.local.local_event_scraper --organiser-id 42               # one specific organiser
python -m services.local.local_event_scraper --mode dry-run                  # discover event URLs, print them, don't crawl/store
python -m services.local.local_event_scraper --mode sanity-check            # 1 event per organiser - quick smoke test across all of them
python -m services.local.local_event_scraper --check-mode url-check         # skip re-crawl of any URL already stored, changed or not
python -m services.local.local_event_scraper --scraper-backend firecrawl    # force Firecrawl's hosted API instead of self-hosted crawl4ai
python -m services.local.local_event_scraper --organiser-id 42 --force-refresh  # re-crawl + re-extract every event for organiser 42, even unchanged ones
```

Flags:
- `--limit N` — only the first N organisers (fewer *organisers*).
- `--organiser-id ID` — just this one organiser.
- `--mode {normal,dry-run,sanity-check}` — `normal` (default) crawls
  everything new; `dry-run` discovers URLs and prints them without
  crawling/storing; `sanity-check` crawls only the first new event per
  organiser (fewer *events per organiser* — the opposite trade-off from
  `--limit`).
- `--check-mode {hash-check,url-check}` — `hash-check` (default) always
  fetches, skips re-extraction only if content is unchanged; `url-check`
  skips entirely (no fetch) if the URL is already stored.
- `--scraper-backend {crawl4ai,firecrawl}` — `crawl4ai` (default) is
  self-hosted/free with an automatic Firecrawl fallback on failure;
  `firecrawl` always uses Firecrawl's hosted API.
- `--force-refresh` — re-crawl and re-extract every event for the selected
  organiser(s), even ones already stored with unchanged content (overrides
  `--check-mode`). For rolling out an extraction-logic fix across
  already-crawled events. Normally combined with `--organiser-id` — without
  it, this re-runs the LLM over every event in the database.

## `services.local.local_feed_importer`

The structured-bulk-feed pipeline (parkrun today; see `DIFFERENCES.md` F5 for
planned Meetup/OSM importers), run in-process — the local-dev equivalent of
`local_event_scraper.py` above, but for the separate pipeline
`feeds/feed_importers.py` registers. Production triggers this via
`server/main.py`'s `/tasks/feed-import` handler instead, from a scheduled
Pub/Sub message.

```
python -m services.local.local_feed_importer --source parkrun
python -m services.local.local_feed_importer --source parkrun --param country="United Kingdom"
```

- `--source NAME` (required) — which registered importer to run (see
  `feeds/feed_importers.py`'s registry).
- `--param key=value` — importer-specific parameter, repeatable; forwarded
  to the importer as a plain dict of strings (each importer parses/validates
  its own params, e.g. parkrun's `country`).

## `services.server.main:app`

Not a one-shot script — a long-running FastAPI server. This is the crawler
tier's real production entrypoint (Pub/Sub push delivers to `/tasks/*` here
on Cloud Run — see `INSTALLATION.md`), but it's also useful locally to
exercise the actual HTTP handlers (e.g. hand-crafting a Pub/Sub push envelope
with `curl`) rather than calling `local_event_scraper`/`local_feed_importer`'s
plain Python functions directly.

```
cd src
poetry run uvicorn services.server.main:app --reload
```

`/healthz`, `/tasks/listing-crawl`, `/tasks/event-crawl`, `/tasks/feed-import`
— see `server/main.py`'s own docstring for the expected Pub/Sub push envelope
shape.

## `services.admin.web.app:app`

Also a long-running FastAPI server, not a script — the on-the-fly admin DB
browser (see `ARCHITECTURE.md`'s "Admin interface" section). Deployed to
Cloud Run as its own service in production (`INSTALLATION.md`), but this is
how you browse the database locally without deploying anything.

```
cd src
poetry run uvicorn services.admin.web.app:app --reload --port 8001
```

Then open `http://localhost:8001/` — organiser index, `/events`
(valid-only tree, filterable by `?organiser_id=`/`?q=`), `/events/invalid`
(same tree, invalid-only — for seeing what the LLM flagged and why), and
`/events/by-type` (sport → race-type → events tree). No auth locally (the
optional `ADMIN_BASIC_AUTH_USER`/`ADMIN_BASIC_AUTH_PASSWORD` gate is only
enforced when both are set in `.env` — see `INSTALLATION.md` for why it
matters once this is deployed, not just run on localhost).
