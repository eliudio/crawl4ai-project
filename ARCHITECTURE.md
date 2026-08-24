# Architecture Overview

```
┌─────────────┐   scheduler (cron)
│ Cloud       │──────────────┐
│ Scheduler   │              ▼
└─────────────┘      ┌──────────────────┐
                     │ queue: listings  │
                     └────────┬─────────┘
                              ▼
                   ┌────────────────────────┐
                   │ Worker: listing-crawl  │  (browser, proxy)
                   │  Playwright/Firecrawl  │
                   └────────┬───────────────┘
                            ▼
                   ┌──────────────────┐
                   │ queue: events    │
                   └────────┬─────────┘
                            ▼
                   ┌────────────────────────┐
                   │ Worker: event-crawl    │  (browser, proxy)
                   │  Playwright/Firecrawl  │
                   └────────┬───────────────┘
                            ▼
                   ┌──────────────────┐
                   │ Database         │
                   │ (Firestore/SQL)  │
                   └──────────────────┘
```

## Phase 1 Implementation (`src/services`)

The diagram above is implemented in `src/services`, independent of the legacy
prototype code elsewhere in `src/`:

- `config.py` / `db.py` / `models.py` — env-driven settings and the
  Postgres schema (`organisers`, `events`, `crawl_runs`).
- `scraper_client.py` — picks which scraper backend actually fetches a page:
  self-hosted `crawl4ai_client.py` by default (free beyond Cloud Run/laptop
  compute), falling back to `firecrawl_client.py`'s hosted API (paid, but
  handles proxy rotation/anti-bot for the rare site that needs it)
  automatically if crawl4ai's own attempt fails. Controlled by
  `SCRAPER_BACKEND` (`crawl4ai` default, or `firecrawl` to always use
  Firecrawl) — see "Running locally" below for the `local_event_scraper.py` flag.
- `llm_extractor.py` — extracts structured event fields from page markdown;
  provider is pluggable (`grok` or `anthropic`) via `LLM_PROVIDER`.
- `listing_crawler.py` / `event_crawler.py` — the two pipeline stages.
  `Organiser.source_type` is the enforcement point for never crawling
  aggregator/platform data: only `source_type=organiser` rows are ever fed
  into event crawling.
- `main.py` — FastAPI app exposing `/tasks/listing-crawl`, `/tasks/event-crawl`
  (the pattern-website pipeline above) and `/tasks/feed-import` (the separate
  structured-bulk-feed pipeline — see "Feed import pipeline" below), meant to
  sit behind Pub/Sub push subscriptions on Cloud Run.
- `seed_organisers.py` — loads `data/organisers_seed.csv` (the organiser
  list below, extracted from findarace.com/racecheck.com) into the
  `organisers` table, since phase 1 has no automated discovery yet. Only ever
  holds organisers for the pattern-website pipeline — a feed-import source's
  own umbrella organiser (parkrun, ...) is bootstrapped by that importer
  itself instead, see below.
- `local_event_scraper.py` — runs the pattern-website pipeline in-process (no
  Pub/Sub), for local development against a local/dev Postgres.
- `feed_importers.py` / `parkrun_import.py` / `local_feed_importer.py` — the
  separate structured-bulk-feed pipeline and its own local-dev runner — see
  "Feed import pipeline" below.
- `Dockerfile` / `requirements.txt` — minimal container for Cloud Run,
  deliberately independent of the repo-root `pyproject.toml`.

### Feed import pipeline (parkrun, and any future structured-bulk-feed source)

Not every event source is a website to scrape. Parkrun (and Meetup/OpenStreetMap,
should those get built - see the TODO sections further down) publish everything as one
structured, ready-to-use feed covering many locations at once - there's no listing page
to discover and no per-event page worth opening. Forcing that shape through the
pattern-website pipeline above (`Organiser.handler` + `listing_crawler.py`) is exactly
what the old "parkrun" handler did, and it needed a real, separately-obtained
authorisation override just to avoid scraping parkrun's own site at all (see git
history) - a sign the fit was wrong, not that the override was.

Instead, this is its own small pipeline, deliberately kept apart from
`discovery_handlers.py`/`listing_crawler.py`:

- `feed_importers.py` — a registry (the same "name -> callable" shape
  `discovery_handlers.py` already uses for the other pipeline) mapping a source name
  (`"parkrun"`, ...) to the importer that owns it end to end: fetch, resolve/create
  whichever `Organiser` row(s) its events belong to, upsert `Event` rows directly.
  Also owns `get_or_create_organiser()`, shared by any importer that represents its
  whole source as one umbrella organiser (parkrun, a future meetup importer) rather
  than one per real-world event host (an OSM-style source would be different - it
  discovers many distinct organisers, one per event, and wouldn't use this helper at
  all). That umbrella row's `source_type` is forced to `PLATFORM` - the same "exists
  for provenance/FK purposes, excluded from the pattern-website pipeline" contract
  `main.py`/`local_event_scraper.py` already enforce for aggregator/platform rows, so
  nothing needs a bespoke check to keep it from ever being picked up by
  `crawl_listing()` again.
- `parkrun_import.py` — the "parkrun" importer. Source of truth is
  [josh-justjosh/parkrun-Cancellations](https://github.com/josh-justjosh/parkrun-Cancellations)'s
  own `events-table.tsv` (built for parkruncancellations.com, MIT-licensed, refreshed
  automatically several times a day) - not parkrun's own `events.json` feed. Fetched as
  a plain, hardcoded `registrator="bot"` (no authorisation-override mechanism, unlike
  the old handler this replaces): reading an openly, unambiguously licensed third-party
  republication hosted on GitHub's own infrastructure is a different act from reading
  parkrun's own site under an unattended crawl, which parkrun's own stated policy
  (parkrun.com/scraping) asks not to happen - see that module's own docstring for the
  reasoning in full.
- `local_feed_importer.py` — runs one importer in-process (no Pub/Sub), the local-dev
  equivalent of `local_event_scraper.py` for this pipeline: `python -m
  services.local_feed_importer --source parkrun`.
- `main.py`'s `/tasks/feed-import` — the production entrypoint, triggered by a Pub/Sub
  message naming which importer to run (`{"source": "parkrun", "params": {...}}`, see
  `pubsub_client.publish_feed_import`) rather than a per-organiser/per-event fan-out -
  meant to sit behind a Cloud Scheduler job (e.g. weekly), not the two per-item queues
  the pattern-website pipeline uses.

### Worked example: parkrun feed import (`main.py`'s `/tasks/feed-import`)

Unlike the pattern-website pipeline's per-organiser/per-event fan-out (below), a feed
importer does everything in one call - there's no separate "discover URLs" stage to
fan anything out from, because the feed already lists everything at once. Worked
through step by step:

1. A scheduled Pub/Sub message (Cloud Scheduler, e.g. weekly) triggers `POST
   /tasks/feed-import` with `{"source": "parkrun", "params": {}}`.
2. `main.py` looks up `"parkrun"` in `feed_importers`' registry, dispatches to
   `parkrun_import.run_import`.
3. `run_import` calls `feed_importers.get_or_create_organiser(...)` - finds the
   existing "parkrun UK" `Organiser` row by name (or creates it, first run), forcing
   its `source_type` to `PLATFORM` either way so it's excluded from the
   pattern-website pipeline's own eligibility checks (`main.py`/`local_event_scraper.py`).
4. `parkrun_import.get_events(...)` fetches and parses
   `events-table.tsv` - checked against robots.txt for real (`registrator="bot"`,
   hardcoded, no override mechanism - see that module's own docstring for why a plain
   bot fetch is the right call for this specific, openly-licensed, third-party-hosted
   source), then builds a full fields dict per row: name, location, exact coordinates,
   which of the two standing weekly schedules applies (via the row's own `Status`
   column).
5. No dedup step: every row is processed every run, new or already-registered -
   `event_crawler.register_event_from_fields(...)` is an upsert, not insert-only, so a
   row already stored just gets replaced in place.
6. For each `(event_url, fields)` pair, `register_event_from_fields(...)` is called
   directly - writing the real `Event`/`EventDistance` rows from the TSV data alone. No
   scrape, no LLM call, no per-page robots.txt check, because there's no per-page fetch
   at all.
7. `run_import` returns a small summary dict (`{"status": "ok", "registered": N,
   "organiser_id": ...}`), logged by `main.py` - there's nothing left to dispatch to a
   second stage the way the other pipeline's `/tasks/listing-crawl` dispatches to
   `/tasks/event-crawl`.

Contrast with the pattern-website pipeline (`listing_crawler.py`/`event_crawler.py`),
where stage 2 (scrape a page + run an LLM extraction) really is slow, costly, and
per-page-failure-prone, and a typical organiser can have dozens to hundreds of events -
that's what still needs fanning out as independent, retryable Pub/Sub messages
(`/tasks/listing-crawl` → N × `/tasks/event-crawl`, see below). A feed importer has no
such stage 2 at all, so there's nothing to fan out.
