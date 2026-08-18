# TODOs

## name and slogan

plebys.com - For the plebs, by the plebs, for free, always 
The wiki for race events

or plebbys.com or pleppys.com or plebies.com

## OpenStreetMap
AI: Try using OpenStreetMap data + community layer as source of event data
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

## APP: event creation
We will want to be able to easily create, through the interface of the app weekly events
to cater for easy registration of parkrun events.
maybe even have some wizard to register races, one of these wizard is supporting parkrun

## parkrun cancellations
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

## Introduce a server based database, google probably, cheap / free
   Then also create some quick way to view events from that database, like the extract but then from that database
   not generated static but dynamic 

## Move the project to rompje.com

## code cleanup

Ask AI to cleanup
1) what is each file and is it still relevant? for example, is grok/key.py still being used? remember: everything
   we currently do starts with scripts from services or tools. So I guess, I'm basically asking if directory 
   grok and events are still relevant
2) introduce code coverage tool
3) write unit tests to cover 100% of code
4) restructure the code, new directory structure
- introduce a subdirectory for common, local and server
- put specific python scripts in these directories, specific to these purposes, common, local and serve
- create directories tools/export with export scripts
- create directories models and move the models in there
- mimick the same directory structure for unit tests

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
  Firecrawl) — see "Running locally" below for the `local_runner.py` flag.
- `llm_extractor.py` — extracts structured event fields from page markdown;
  provider is pluggable (`grok` or `anthropic`) via `LLM_PROVIDER`.
- `listing_crawler.py` / `event_crawler.py` — the two pipeline stages.
  `Organiser.source_type` is the enforcement point for never crawling
  aggregator/platform data: only `source_type=organiser` rows are ever fed
  into event crawling.
- `main.py` — FastAPI app exposing `/tasks/listing-crawl` and
  `/tasks/event-crawl`, meant to sit behind Pub/Sub push subscriptions on
  Cloud Run.
- `seed_organisers.py` — loads `data/organisers_seed.csv` (the organiser
  list below, extracted from findarace.com/racecheck.com) into the
  `organisers` table, since phase 1 has no automated discovery yet.
- `local_runner.py` — runs the whole pipeline in-process (no Pub/Sub), for
  local development against a local/dev Postgres.
- `Dockerfile` / `requirements.txt` — minimal container for Cloud Run,
  deliberately independent of the repo-root `pyproject.toml`.

### Worked example: parkrun listing-crawl dispatch (`registrator` "bot" vs a real person)

`listing_crawler.crawl_listing()`'s whole job is to answer *"what needs doing?"*
cheaply and quickly (one request, no LLM) - completely separately from *"do it,"*
which then gets fanned out as independent Pub/Sub messages (`main.py`'s
`/tasks/listing-crawl` handler, below), each independently retryable and
parallelizable across Cloud Run instances. That split exists because stage 2 (scrape a
page + run an LLM extraction) is the slow, costly, per-page-failure-prone part, and a
typical organiser can have dozens to hundreds of events - a single inline loop inside
one HTTP handler invocation can't give that independent retry/parallelism, and would
risk blowing past any sane request timeout.

`_parkrun_handler` (`Organiser.handler == "parkrun"`) is the one handler where this
genuinely differs depending on `Organiser.registrator` - see `Organiser.registrator`'s
own docstring in `models.py` for what that field means (`"bot"`: an unattended
automated crawl, always respecting robots.txt; anything else names a real person who
has separately, directly obtained the site owner's own permission to collect the
data). Worked through step by step, for organiser 1 (parkrun UK):

**`registrator == "bot"`** (the default - an unattended automated crawl):

1. A Pub/Sub message triggers `POST /tasks/listing-crawl` with `organiser_id=1`.
2. `main.py` loads the organiser, calls `listing_crawler.crawl_listing(session, organiser)`.
3. `crawl_listing()` looks up `organiser.handler` (`"parkrun"`) in
   `discovery_handlers`, dispatches to `_parkrun_handler`.
4. Since `registrator == "bot"`, `_parkrun_handler` calls
   `parkrun_feed.get_event_urls(...)` - this checks robots.txt for real, fetches
   `events.json`, and constructs each event's URL from `base_url + eventname + "/"`.
5. `_filter_new_urls` drops any URL already in the `events` table, keeping only
   genuinely new ones.
6. That filtered list is returned all the way back up to `main.py`.
7. `main.py` loops over it: `for url in new_urls: pubsub_client.publish_event_crawl(organiser_id, url)`
   - **one Pub/Sub message per URL**.
8. Each of those messages, independently and later (possibly in parallel, possibly on
   a different Cloud Run instance), triggers its own `POST /tasks/event-crawl`, which
   calls `event_crawler.crawl_event(session, organiser_id, event_url)` - the real
   scrape + LLM extraction, per event.

**`registrator != "bot"`** (a real person's name, e.g. `"johan"` - the override active):

Steps 1-3 are identical. From there:

4. `_parkrun_handler` calls `parkrun_feed.get_events(...)` instead of
   `get_event_urls`. robots.txt is skipped entirely this time
   (`robots.is_allowed()` returns `True` immediately for a non-`"bot"` registrator,
   never even fetching robots.txt) - and for each feature it builds the *whole* fields
   dict (`build_event_fields`: name, location, exact coordinates, distance, the
   standing weekly schedule, everything), not just a bare URL.
5. No dedup step - `_filter_new_urls` is never called in this branch. Every feature
   from the feed is processed every time this runs, new or already-existing
   (`register_event_from_fields` is an upsert, not insert-only).
6. For each `(event_url, fields)` pair, `event_crawler.register_event_from_fields(...)`
   is called **immediately, inline** - writing the real `Event`/`EventDistance` rows
   using only the JSON data. No scrape, no LLM call, no per-page robots.txt check,
   because there's no per-page fetch at all.
7. One `CrawlRun` row + one console line get logged directly from inside
   `_parkrun_handler` itself (e.g. *"parkrun UK: 1417 event(s) registered directly
   from parkrun feed (registrator='johan')"*) - printed right here specifically
   because the generic "enqueued N event(s)" line in step 10 is always 0 for this
   branch and would otherwise read as "nothing happened."
8. `_parkrun_handler` returns `[]` - not "nothing found," but "nothing left to
   dispatch," since everything already got done in step 6.
9. Back in `main.py`, the publish loop (`for url in new_urls: pubsub_client.publish_event_crawl(...)`)
   runs **zero times** - no `event-crawl` messages get published for any of these
   events. Steps 7-8 from the `"bot"` case (dispatch, then separately trigger
   `/tasks/event-crawl`) never happen at all.
10. `main.py`'s own final print (`enqueued {len(new_urls)} event(s)`) reports
    "enqueued 0 event(s)" for the same reason `local_runner.py`'s own generic "0 new
    event URL(s)" line does - it's honestly answering "how many are left to dispatch
    separately," which is correctly zero; step 7 above is what actually carries the
    real count.

**`registrator != "bot"` is a deliberate exception to the fan-out design, not a
shortcut around it**: it's the one handler where there genuinely is no stage 2 to fan
out, because `events.json` already contains everything needed to build every `Event`
in one shot. Every other handler (and parkrun's own `"bot"` path) keeps the two-stage
split because their stage 2 is real, separate, per-page work that needs the
independent retry/parallelism dispatching it as N messages provides.

**Production has no sanity-check-equivalent lever yet**: `main.py` never passes
`dry_run`/`event_limit` to `crawl_listing()` at all - only `local_runner.py`'s `--mode
sanity-check`/`--mode dry-run` compute and pass those (see `local_runner.py`'s own
`run()`). A real listing-crawl Pub/Sub message against parkrun with the override
active always processes the *entire* current feed for real, every time it fires,
until/unless that gets added to `main.py` too.

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
poetry run python -m services.local_runner --limit 3
```
Add `--scraper-backend firecrawl` to force Firecrawl's hosted API instead of
the self-hosted `crawl4ai` default (e.g. to compare the two, or if
self-hosting is misbehaving on a given organiser) — see `scraper_client.py`.

### Deploying (GCP)

```
gcloud pubsub topics create listing-crawl event-crawl
docker build -f src/services/Dockerfile -t <region>-docker.pkg.dev/<project>/crawler/pipeline .
docker push <region>-docker.pkg.dev/<project>/crawler/pipeline
gcloud run deploy crawler-pipeline --image <...> --set-env-vars <...>
gcloud pubsub subscriptions create listing-crawl-push --topic listing-crawl --push-endpoint <run-url>/tasks/listing-crawl
gcloud pubsub subscriptions create event-crawl-push --topic event-crawl --push-endpoint <run-url>/tasks/event-crawl
poetry run python -m services.seed_organisers --publish   # seed organisers table + kick off first crawl
gcloud scheduler jobs create pubsub recrawl-organisers --schedule="0 3 * * *" --topic=listing-crawl --message-body='...'  # per-organiser recrawl trigger
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


## REMARKS

* When using a registrator other than "bot", parkrun handler will ignore robots.txt. It will not be scraping the pages, but it will use the json file with all events and construct the events with that. The events will be registered with the registrator specified. This is "pretend" pleb registration.
