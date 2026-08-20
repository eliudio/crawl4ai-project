# TODOs

## name and slogan

plebys.com - For the plebs, by the plebs, for free, always 
The wiki for race events

or plebbys.com or pleppys.com or plebies.com

## We need, for price comparison, the price as a value, not string + ccy 

## APP: Map with search results + the price, like booking.com.

## When a bot creates an event, or an organisation, ... it needs to be
   able to add a comment / note / source like "retrieved from url", for example
   retrieved from github

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

## bug
AI: It seems some events have no location, yet the location is given. For example, the location for
https://www.zigzagrunning.co.uk/event-details/two-hundred-miles-challenge is determined to be www.evententry.com
That makes no sense. Perhaps this is the best we can do, if we don't want to spend too much. But perhaps this is 
an easy fix, basically we want events where no location exists to be : unknown location

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

## Community
User reputation / levels: New or low-contribution accounts have less weight. Higher-level users’ reports carry more influence.
Community verification: Other drivers confirm (“thumbs up” / still there) or deny (“not there”). Multiple independent confirmations strengthen a report; repeated denials weaken it.
Technical measures: De-duplication (same-location/time reports from one source don’t stack easily), location/time stamping, and detection of suspicious patterns (e.g., rapid repeated reports from one account). Ghosting or shadow-banning can hide a user’s reports from others without fully banning the account.
Policy enforcement: Fake/spam reports violate community terms. Persistent abuse can lead to temporary or permanent restrictions. Community editors and Waze staff can investigate repeated problems from the same username.
No single-user instant override: One person marking “not there” (including an officer trying to clear their own presence) usually only affects their own view or slightly shortens the report’s life; several independent “not there” votes are typically needed to clear it for everyone.

## APP: event creation
We will want to be able to easily create, through the interface of the app weekly events
to cater for easy registration of parkrun events.
maybe even have some wizard to register races, one of these wizard is supporting parkrun

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

## question
   parkrun question: what happens when we re-run parkrun? We need to somehow verify if the events in the json correspond to the
   entries in the database. Do we do so?

   Partially, as of parkrun_import.py: every row in the current TSV, for the configured
   country, gets re-registered (upsert, keyed by URL) on every run, so an existing
   event's fields stay in sync with the feed. Not handled: a parkrun location that
   disappears from the feed entirely (permanently closed, say) leaves its old `Event`
   row in the database untouched, with nothing to mark it stale/removed - still open.

## Introduce a server based database, google probably, cheap / free
   Then also create some quick way to view events from that database, like the extract but then from that database
   not generated static but dynamic

## Unit test coverage
introduce code coverage tool / write unit tests to cover 100% of code

## run on google

Is the current project work-able to deploy on google?
I have started this as a google project and local_runner
Then I have iterated over it, running local_runner
Now that local_runner works, I want to verify how it can be run on google
Did we make change on local_runner that need to be applied for google.

# llm consoles

https://console.x.ai
https://console.anthropic.com

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

### Running locally

#### setup locally

1. Download, install and run docker desktop
2. Install Firecrawl: (self hosting not used). 
```
C:\src
mkdir firecrawl2 
cd C:\src\firecrawl2
git clone https://github.com/mendableai/firecrawl.git
cd firecrawl
npm install
```

3. Install postgres
```
docker run -d --name events-db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=events -p 5432:5432 postgres:16
```

4. Install .env
```
cp src/services/.env.example src/services/.env
```
Provide values for
* DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/events
* GROK_API_KEY=your grok api key

#### run locally

1. Start docker desktop on your laptop
2. Run firecrawl (self hosting not used). 
```
cd C:\src\firecrawl2\firecrawl
docker-compose up
```

3. Run postgres
```
docker start events-db && docker ps --filter "name=events-db" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"```
```

4. Some useful interactions with database

4.1 Drop all data from postgres
```
docker exec -it events-db psql -U postgres -c "DROP DATABASE events;"
docker exec -it events-db psql -U postgres -d postgres -c "CREATE DATABASE events;"
```

4.2 Describe all table
```
docker exec -it events-db psql -U postgres -d events -c "
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;
"
```

5. run service
```
cd src
poetry run python -m services.local_event_scraper --limit 3
```
Add `--scraper-backend firecrawl` to force Firecrawl's hosted API instead of
the self-hosted `crawl4ai` default (e.g. to compare the two, or if
self-hosting is misbehaving on a given organiser) — see `scraper_client.py`.

6. run a feed importer (parkrun, ...) - the separate pipeline, see "Feed import
   pipeline" above; not part of the pattern-website `local_event_scraper.py` run above
```
poetry run python -m services.local_feed_importer --source parkrun
```

### Deploying (GCP)

```
gcloud pubsub topics create listing-crawl event-crawl feed-import
docker build -f src/services/Dockerfile -t <region>-docker.pkg.dev/<project>/crawler/pipeline .
docker push <region>-docker.pkg.dev/<project>/crawler/pipeline
gcloud run deploy crawler-pipeline --image <...> --set-env-vars <...>
gcloud pubsub subscriptions create listing-crawl-push --topic listing-crawl --push-endpoint <run-url>/tasks/listing-crawl
gcloud pubsub subscriptions create event-crawl-push --topic event-crawl --push-endpoint <run-url>/tasks/event-crawl
gcloud pubsub subscriptions create feed-import-push --topic feed-import --push-endpoint <run-url>/tasks/feed-import
poetry run python -m services.seed_organisers --publish   # seed organisers table + kick off first crawl
gcloud scheduler jobs create pubsub recrawl-organisers --schedule="0 3 * * *" --topic=listing-crawl --message-body='...'  # per-organiser recrawl trigger
gcloud scheduler jobs create pubsub feed-import-parkrun --schedule="0 4 * * 1" --topic=feed-import --message-body='{"source": "parkrun", "params": {}}'  # weekly
```

Cloud SQL (Postgres) is the target for `DATABASE_URL` in production;
`init_db()` creates tables directly for now — move to Alembic migrations
once the schema stabilizes.

## Current State

We want to create a database with sports event details. Sports events such as running (initially), 
cycling, and other sports events. The database should contain event details such as location, price, description, and 
more specific details like distance, age, ...

There are online database available. There are 2 distinct groups of providers:
2) the event organiser: these are companies and organisations who organise sports events. 
Example: https://www.runthrough.co.uk/, https://www.onerace.events/events
2) the event aggregators: these are companies that actually collect events from the internet and make them available 
online in 1 big database. Example: https://findarace.com/
3) the event platforms: these are companies that allow event organisers to host their event on their platform

We are interested in gathering events from events organisers. The purpose of this database is to be used for our
own aggregator project. We do not want to steal events from the event aggregator databases themselves. However,
we might want to use event aggregators to find out which event organisers exists. But we will never actually use
the details provided from the aggregators. The same with event platforms. The latter is harder to detect, but we
should try to exclude.

I have this prototype project "crwarl4ai project" today. This illustrates this intend.

The prototype currently uses 
- python as language
- firecrawl to crawl, browse, interact with the internet / interpret contents of pages 
- grok AI to interpret contents of pages
- express VPN to remain anonymous and not look like a robot
- sqlite3 to store results 

The prototype currently runs on a laptop inside pycharm.

The prototype currently is fed a list of URLS. Each URL represents a home page with a list of events.
These events are links to event details pages.

Improvements: 
1) We actually want the prototype to be extended: the list of URLs itself should not be provided but 
found from events aggregators. The way this would go is 
1.1) we'd go to the event aggregator and find events. For example:  https://findarace.com/10k-runs
1.2) from there we find races, For example: https://findarace.com/events/the-one-in-the-park-hyde-park
1.3) from there we find the organiser page. For example: https://findarace.com/onerace
1.4) from there we find the organiser home page. For example https://www.onerace.events/
1.5) from there we find the events. For example https://www.onerace.events/events

This improvement is possibly something we will do in phase 2 of the project, as this searching and finding 
URLs is potentially different per event aggregator, and hence more bespoke solution.

So initially we will probably stick with a list of manually retrieved list of event organiser event URLs.

I want to implement this differently so that it becomes scalable, more performant and not reliant on a laptop to run.
I don't mind which technology, language or architecture. It must run on a host, e.g. Google Firebase.
Let's start with brainstorming around what architecture is best suited for this.

Do NOT consider anything of the existing code. The new solution will be built from scratch, the optimal architecture 
which is not based on the existing code. Just the ideal architecture, tech and language. 

Which platform(s) will be best? What will run where?

# Actions 
- buy claude.com antropic or use grok (to be decided) 
- drop claude.ai subscription
- Add type of race, length of race, add frequency: yearly, monthly, weekly, daily, single event


# Requirements

Initially:
1. Scrape events from as many organisers as possible. Server side. 24x7 scraping. Don't copy aggregators, only organisers. Respect robots.txt
2. Scrape aggregators to find organisers. But ONLY use it as a source of organiser names or domain names of organisers
Race db. Find a race
3. Indicate races you've done, with time, ... Upload photos, ... Link to Strava / Garmin
4Community of people
5Map overview, with races, flags where you've ran, where other's ran, ...

Then:
1. A booking system, like Booking.com for races.
   * (small, local) organizers can create a race
   * no commission
   * members can book to join
   * some races are only references
2. Stripe pay direct to the customer

# To consider event sources:
* runningcalendar.co.uk
* englandathletics.org/runevents/
* runabc.co.uk
* running.org 
* etchrock.com
* racedirectorshq.com/gb/directory/
* Trail Running Association - https://www.tra-uk.org/
* Centurion Running - https://www.centurionrunning.com/
* Ultra X - https://ultra-x.co/
* British Triathlon - https://www.britishtriathlon.org/
* Challenge Family - https://www.challenge-family.com/
* IRONMAN - https://www.ironman.com/
* Spartan Race (UK) - https://uk.spartan.com/en/
* Tough Mudder (UK) - https://uk.toughmudder.com/
* The Wolf Run - https://www.thewolfrun.com/
* Gravel and Grit Events
* Howling Events Ltd.
* MG SPORT
* MOAR Events Ltd
* Nice Work Partner Races on behalf of the Romney Marsh Rotary Club,https://www.nice-work.org.uk/ (rotary club: https://romneymarshrotary.co.uk/)
* One More Lap
* UK TRAINING CLUB
* VRM TEAM ASD
* Challenging Events
* KS-Client Events
* Phoenix Running Bedfordshire,https://www.phoenixrunning.co.uk/
* Rory Macpherson
* Run Rugged Events
* Ultra Violet Ltd
* Well Run
* Epic Endurance Events CIC,https://www.epicenduranceevents.co.uk/
* Phoenix Running West Sussex,https://www.phoenixrunning.co.uk/
* Phoenix Running Hampshire,https://www.phoenixrunning.co.uk/
* EnduroTrek,https://www.endurotrek.co.uk
* endurotrek,https://www.endurotrek.co.uk
* Every Mile Counts
* endurosport LLP
* Phoenix Running Cambridgeshire,https://www.phoenixrunning.co.uk/
* Nice Work Partner Races,https://www.nice-work.org.uk/
* Phoenix Running Manchester,https://www.phoenixrunning.co.uk/
* UK Cycling Events
* OP Events
* Big Bear Events
* Phoenix Running South Wales,https://www.phoenixrunning.co.uk/
* The Clubhouse
* Nice Work,https://www.nice-work.org.uk/
* Rocket Race
* Curley's Leisure / The ROC Triathlon
