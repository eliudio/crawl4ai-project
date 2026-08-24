# App description

## Overview
We want to become something like to booking.com for sports events. 
Although, we don't have to own the hosting of the events. The main purpose of the app
is to fill a gap in the market: when I want to run, I currently have to go and search 
many websites and apps, to find a local run. And I'll be lucky to find one. But,
there are plenty of races being organised, there isn't 1 database globally around the
world which covers all of them. The existing ones have a business model that doesn't focus
on this. Their business model is about hosting events and making money from participating.
That's not what this app is about. This app is to cater for the needs of the runner, of the
crowd. 

We want to create a database with sports event details. Sports events such as running (initially), 
cycling, and other sports events. The database should contain event details such as location, price, description, and 
more specific details like distance, age, ...

## Crowd sourcing

The event data should be crowd sourced, i.e. like wikipedia, or openstreetmap, or wayze, 
we allow users to register data.

## Bot sourcing

But we want to bootstrap this a bit with bots also, which crawl the internet and find events. 
More of this below.

There are online database available. There are 2 distinct groups of providers:
1) the event organiser: these are companies and organisations who organise sports events. 
Example: https://www.runthrough.co.uk/, https://www.onerace.events/events
2) the event aggregators: these are companies that actually collect events from the internet and make them available 
online in 1 big database. Example: https://findarace.com/
3) the event platforms: these are companies that allow event organisers to host their event on their platform

We are interested in gathering events from events organisers. The purpose of this database is to be used for our
own aggregator project. We do not want to steal events from the event aggregator databases themselves. However,
we might want to use event aggregators to find out which event organisers exists. But we will never actually use
the details provided from the aggregators. The same with event platforms. The latter is harder to detect, but we
should try to exclude.

### Aggregator -> Organiser
One way of gathering organizers of events is by first finding the organisers from 
aggregator sites. We do not want to expose these aggregators to the outside world 
of source. But we can internally store and maintain all organisers, and from there
we can scrape the events.

So for example
* We'd go to the event aggregator and find events. For example:  https://findarace.com/10k-runs
* from there we find races, For example: https://findarace.com/events/the-one-in-the-park-hyde-park
* from there we find the organiser page. For example: https://findarace.com/onerace
* from there we find the organiser home page. For example https://www.onerace.events/
-> Voila this is the organiser

With the organiser, we can now freely scrape the events. For example https://www.onerace.events/events

### Initial phase of the project
In an initial phase of the project, we will manually retrieved list of event organiser event URLs (home pages).

## Example functionalities
- Map with search results + the price, like booking.com.
- keep history of changes to event data (or all data for that matter)
- allow to mass undo a change, if a hacker / maliscious member has been identified
- Community: User reputation / levels: New or low-contribution accounts have less weight. Higher-level users’ reports carry more influence.
Community verification: Other drivers confirm (“thumbs up” / still there) or deny (“not there”). Multiple independent confirmations strengthen a report; repeated denials weaken it.
Technical measures: De-duplication (same-location/time reports from one source don’t stack easily), location/time stamping, and detection of suspicious patterns (e.g., rapid repeated reports from one account). Ghosting or shadow-banning can hide a user’s reports from others without fully banning the account.
Policy enforcement: Fake/spam reports violate community terms. Persistent abuse can lead to temporary or permanent restrictions. Community editors and Waze staff can investigate repeated problems from the same username.
No single-user instant override: One person marking “not there” (including an officer trying to clear their own presence) usually only affects their own view or slightly shortens the report’s life; several independent “not there” votes are typically needed to clear it for everyone.
- event creation: We will want to be able to easily create, through the interface of the app weekly events
to cater for easy registration of parkrun events.
maybe even have some wizard to register races, one of these wizard is supporting parkrun
- A booking system, like Booking.com for races.
   * (small, local) organizers can create a race
   * no commission
   * members can book to join
   * some races are only references
- Stripe pay direct to the customer


# Solution

So on the one hand we want an app and a website allowing users to find events (find near me, next weekend, ...)
Possibly flutter app for APP and website for user interaction
On the other hand we want a crawl solution, probably a cloud solution.
The crawling solution must be a scalable solution
We want a 24x7 online solution scraping events continuously, i.e. on cloud.

# name and slogan

* plebys.com - For the plebs, by the plebs, for free, always 
The wiki for race events

or plebbys.com or pleppys.com or plebies.com

runafish.com

# todo's

## parkrun cancellations
Still open - not implemented by parkrun_import.py. The scraping-a-per-country-page
approach described below is moot now (we no longer touch parkrun.com/images.parkrun.com
at all - see "Feed import pipeline"), but the underlying feature isn't done: the new
source (events-table.tsv, see parkrun_import.py) already carries its own
`Cancellations` column per row - every row sampled while building this importer had
it empty ("[]"), so its real shape/values are still unconfirmed. Whoever picks this
up: map that column into `Event.lifecycle_status`/`lifecycle_text` (see models.py's
EventLifecycle) instead of parsing a country-language cancellations page.

Unless  parkrun.com/robots.txt doesn't allow this, which is the case, so skip this for now 

https://images.parkrun.com/events.json
has an entry "countries". This has a list of all url's for each country, e.g. https://www.parkrun.org.uk
If you append cancellations to these url's you get for each country the cancelled events
This is in the language of the country, the format of this page is 
- Date in that language, e.g.  lørdag den 22. august 2026
and then a list of locations, e.g. https://www.parkrun.dk/holbaekfaelled/ and https://www.parkrun.dk/lyngby/

Support this, i.e. when processing parkrun, make sure to flag the cancelled events in the database as 
cancelled and uncancel the ones that perhaps were cancelled before.
Unless  parkrun.com/robots.txt doesn't allow this, which is the case, so skip this for now 

## We need, for price comparison, the price as a value, not string + ccy 

## When a bot creates an event, or an organisation, ... it needs to be
able to add a comment / note / source like "retrieved from url", for example
retrieved from github

## Unit test coverage
introduce code coverage tool / write unit tests to cover 100% of code

## add history

We need to know the contributors to an event.
And we also like to know the history that makes mass-revert possible, just in case a hacker
does mass damage and we want to revert. Below are the table changes and extra tables to
support this functionality

As suggested by grok: 

Here is the concrete schema extension that matches how Wikipedia, Wikidata and OpenStreetMap actually operate.

1. Minimal users table (required foundation)
```
SQLCREATE TABLE users (
  id            serial PRIMARY KEY,
  -- whatever identity fields you already use (email, username, etc.)
  created_at    timestamptz NOT NULL DEFAULT now()
);
```

2. Attribution + versioning columns on the crowd-maintained tables

We need to keep history of people reporting stuff.

Add these columns to organisers, events and event_occurrences:

```
SQL-- on organisers (you already have created_at / updated_at)
ALTER TABLE organisers
  ADD COLUMN created_by   integer REFERENCES users(id),
  ADD COLUMN updated_by   integer REFERENCES users(id),
  ADD COLUMN version      integer NOT NULL DEFAULT 1,
  ADD COLUMN deleted_at   timestamptz,
  ADD COLUMN deleted_by   integer REFERENCES users(id);

-- on events
ALTER TABLE events
  ADD COLUMN created_by   integer REFERENCES users(id),
  ADD COLUMN created_at   timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN updated_by   integer REFERENCES users(id),
  ADD COLUMN updated_at   timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN version      integer NOT NULL DEFAULT 1,
  ADD COLUMN deleted_at   timestamptz,
  ADD COLUMN deleted_by   integer REFERENCES users(id);

-- on event_occurrences
ALTER TABLE event_occurrences
  ADD COLUMN created_by   integer REFERENCES users(id),
  ADD COLUMN created_at   timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN updated_by   integer REFERENCES users(id),
  ADD COLUMN updated_at   timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN version      integer NOT NULL DEFAULT 1,
  ADD COLUMN deleted_at   timestamptz,
  ADD COLUMN deleted_by   integer REFERENCES users(id);
```
version is incremented on every successful write and is used for optimistic concurrency. Soft-delete is done by setting deleted_at / deleted_by rather than hard-deleting the row.

3. Central contribution log (the history that makes mass-revert possible)
```
SQLCREATE TABLE contributions (
  id              bigserial PRIMARY KEY,
  entity_type     text NOT NULL,               -- 'organiser' | 'event' | 'event_occurrence'
  entity_id       integer NOT NULL,
  user_id         integer REFERENCES users(id),
  action          text NOT NULL,               -- 'create' | 'update' | 'delete' | 'restore' | 'mass_revert'
  version         integer NOT NULL,            -- the version this contribution produced
  comment         text,                        -- edit summary
  changes         jsonb,                       -- before/after or field-level diff
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX contributions_entity_idx
  ON contributions (entity_type, entity_id, created_at DESC);

CREATE INDEX contributions_user_idx
  ON contributions (user_id, created_at DESC);
```
Every create, update or soft-delete of an organiser, event or occurrence writes one row here. The live row always reflects the latest version; the contribution table holds the complete ordered history.

4. Optional but useful: changesets (grouping related edits)
When a user edits an organiser together with its events and occurrences in one session, group them:
```
SQLCREATE TABLE changesets (
  id              bigserial PRIMARY KEY,
  user_id         integer NOT NULL REFERENCES users(id),
  comment         text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  closed_at       timestamptz
);

ALTER TABLE contributions
  ADD COLUMN changeset_id bigint REFERENCES changesets(id);
```

5. How mass-revert of one user works with this schema

* Query contributions for every row where user_id = X, ordered by created_at DESC.
* For each distinct (entity_type, entity_id) that the user touched, locate the contribution that immediately preceded their first change (or the last good version before their consecutive edits).
* Restore the live row to that earlier state, increment its version, set updated_by to the admin performing the revert, and write a new contribution with action = 'restore' (or 'mass_revert') and a clear comment.
* The original malicious contributions stay in the table forever; they are simply no longer the current version of the entity.

This is exactly the pattern used by MediaWiki (revision history + rollback) and OpenStreetMap (versioned objects + changeset reverts): the live data is restored, the full provenance remains, and a single user’s damage can be undone cleanly while preserving everyone else’s work.

# Data sources

## meetup

Can we retrieve events from meetup?

https://grok.com/share/c2hhcmQtMw_04a30cea-98f0-434a-bfa2-2e7ca294cf3

## OpenStreetMap

AI: I want to consider using OpenStreetMap data + community layer as source of event data
Using the Overpass API with targeted queries is the intended and accepted way to pull specific features such as 
network=parkrun or operator=Parkrun.

What would the result be if I do so today, now?

Don't change anything, just answer to see if OSM has properly populated some events and if this is a source 
for querying. Just brainstorming.


# bugs

## bug Event location
AI: It seems some events have no location, yet the location is given. For example, the location for
https://www.zigzagrunning.co.uk/event-details/two-hundred-miles-challenge is determined to be www.evententry.com
That makes no sense. Perhaps this is the best we can do, if we don't want to spend too much. But perhaps this is 
an easy fix, basically we want events where no location exists to be : unknown location

## Introduce a server based database, google probably, cheap / free
Then also create some quick way to view events from that database, like the extract but then from that database
not generated static but dynamic


# Server hosted

## Architecture Overview

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
