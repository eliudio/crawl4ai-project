# TODOs

## name and slogan

pleppys.com
For the plebs, by the plebs, for free, always

## An event can have some extra fields:
   - subscription opening and closing date and time

   extend the events table and cater for this 

## Introduce a server based database, google probably, cheap / free
   Then also create some quick way to view events from that database, like the extract but then from that database
   not generated static but dynamic 

## Include the weekly events / runs and club runs which might not be all that clear to scrape, like parkrun.co.uk

## Move the project to rompje.com

## Change the name of the project to eyeonrace.com ?

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
docker exec -it events-db psql -U postgres -d events -c "
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;
"

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
