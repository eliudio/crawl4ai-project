# Architecture Overview

> Derived from `README.md` (product vision + the existing "Server hosted" crawler
> design it already contained). This is the target architecture for the product
> `README.md` describes, not a description of what's implemented today — see
> `DIFFERENCES.md` for the gap between this and the current code.

## Product vision, in one paragraph

A global, crowd-sourced (Wikipedia/OpenStreetMap/Waze-style) database of sports
events — running first, then cycling and others — covering *any* event an
organiser puts on, not just the ones that pay an aggregator to be listed.
Bootstrapped with bots that crawl organiser sites directly (never aggregator or
platform sites, which are only ever used to *discover* organisers). Exposed to
end users through a map/search app and website (find-near-me, like
booking.com), with an optional zero-commission booking layer on top, and kept
healthy long-term the way OSM/Wikipedia are: full edit history, versioning,
and mass-revert.

## Components and how they interact

There are four tiers. The client tier only ever talks to the backend API; the
backend API and the crawler/bot tier are the only two things that talk to the
database directly.

```
┌────────────────────────────┐        ┌──────────────────────────────┐
│ Client tier                │        │ Crawler / bot-sourcing tier   │
│  - Flutter app             │        │  (see "Crawler tier" below     │
│  - Website                 │        │   for the two pipelines)       │
└──────────────┬─────────────┘        └───────────────┬────────────────┘
               │ HTTP(S)                                │ writes
               ▼                                        ▼
      ┌─────────────────────────────┐         ┌────────────────────┐
      │ Backend API                 │◄────────│ Database           │
      │  - search / map / "near me" │  reads  │  (Postgres / Neon) │
      │  - crowd-sourced edits      │────────►│                    │
      │  - auth, reputation,        │  writes │  organisers        │
      │    verification             │         │  events /          │
      │  - contribution history /   │         │   occurrences /    │
      │    mass-revert              │         │   distances        │
      │  - booking + Stripe         │         │  users / contrib-  │
      └──────────────────────────────┘         │   utions /         │
                                                │   changesets       │
                                                │  crawl_runs        │
                                                └────────────────────┘
```

The database is the single shared source of truth: the crawler tier
bot-sources rows into it (`registrator = "bot"` or a named person's override),
end users crowd-source edits into the same tables through the backend API
(`registrator = <user>`), and every write — bot or human — is expected to go
through the same attribution/versioning contract (see "History &
crowd-sourcing" below) so a bad batch from either source can be identified and
mass-reverted the same way.

### Client tier

- **Flutter app** (mobile) and a **website** (web) are the two user-facing
  surfaces README calls out. Both are thin clients over the backend API — no
  direct database access, no crawling logic.
- User-facing functionality: map + search results with price (find near me,
  next weekend, ...), event detail pages, a booking flow, and the
  community/contribution UI (submit an edit, confirm/deny another user's
  report, view an event's history).
- An event-creation "wizard" flow lives here too, including a dedicated
  parkrun-style wizard for registering recurring weekly events.

### Backend API

- The application server behind the client tier. README doesn't name a
  specific framework for this tier (unlike the crawler tier, where FastAPI is
  explicit) — treat the framework choice as open, but the responsibilities are
  fixed by README:
  - Search/map queries (geo + free-text + filters) over `events` /
    `event_occurrences`.
  - Authenticated read/write access for crowd-sourced contributions —
    create/update/soft-delete on `organisers` / `events` / `event_occurrences`,
    enforcing the optimistic-concurrency `version` column.
  - User accounts, reputation/level, and community verification
    (thumbs-up/"not there", de-duplication of near-identical reports,
    abuse/rate-limit detection, ghosting/shadow-ban).
  - Contribution history per entity and per user, and an admin mass-revert
    operation (see "History & crowd-sourcing" below).
  - Booking: organisers create a race listing (or it exists as a reference-only
    bot-sourced row), members book onto it, no commission is taken, payment
    goes through Stripe direct to the organiser/customer rather than being
    held by the platform.
- This tier is additive to, and separate from, the crawler tier's own small
  internal task API (`/tasks/*` in the crawler tier below) — the latter is
  never exposed to end users, only to the scheduler/queue infrastructure.

### Database

- Postgres, hosted on **Neon** (serverless Postgres) — the schema README gives
  concrete DDL for. Core tables: `organisers`, `events`, `event_occurrences`,
  `event_distances`, `crawl_runs`.
- Neon is a managed, standard-wire-protocol Postgres, so nothing above the
  connection string changes — any Postgres client (SQLAlchemy/psycopg, as
  already used) works unmodified. What Neon specifically brings that a plain
  self-run Postgres wouldn't: serverless autoscaling/scale-to-zero compute,
  branchable databases (a full, cheap copy-on-write branch of the DB per dev/
  preview environment), and a built-in pooled connection endpoint meant for
  exactly this architecture's bursty, many-short-lived-connections shape
  (Cloud Run task workers, scheduled feed-import jobs) rather than a small
  number of long-lived connections.
- History/crowd-sourcing tables layered on top (README's "add history"
  section, modelled directly on MediaWiki/OSM):
  - `users` — minimal identity table; every contribution attributes to a row
    here (including `registrator = "bot"` conceptually mapping to a system
    user, or the bot acting on its own service identity).
  - `created_by` / `updated_by` / `version` / `deleted_at` / `deleted_by` on
    every crowd-maintained table (`organisers`, `events`,
    `event_occurrences`) — optimistic concurrency + soft delete.
  - `contributions` — one row per create/update/delete/restore, `entity_type`
    + `entity_id` + `user_id` + `version` + free-text `comment` + a
    before/after `changes` JSON diff. This is what makes per-user mass-revert
    possible: find every contribution by a user, restore each touched entity
    to the version just before that user's edits, log the restore itself as a
    new contribution.
  - `changesets` — optional grouping of related edits made in one session.
  - A structured price value (`price_amount` + `price_currency`), not just
    free text, so cross-listing price comparison is possible.
- Firestore was floated in README as a cheaper/free managed alternative, but
  every concrete schema example in README (and the versioning/history design
  in particular, which leans on relational FKs, joins and `jsonb`) is
  Postgres — treat Postgres-on-Neon as the primary target and Firestore as a
  rejected/parked alternative, not a second store to keep in sync.

### Crawler / bot-sourcing tier

Two independent pipelines feed the same database, both scheduled and running
as 24×7 cloud jobs (no long-lived server to keep up):

**1. Pattern-website pipeline** — for organiser sites with a normal
listing-page-then-event-page structure:

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
                   │  + LLM extraction      │
                   └────────┬───────────────┘
                             ▼
                   ┌──────────────────┐
                   │ Database         │
                   └──────────────────┘
```

- Only `organiser`-classified sources are ever crawled for event data.
  Aggregator and platform sites are used solely to *discover* organiser
  homepages (aggregator listing page → race page → organiser page → organiser
  homepage), never as a source of event details — that data is never stored.
  In the initial phase this discovery step is manual (a hand-maintained list
  of organiser homepage URLs); full aggregator→organiser automation is a later
  phase.
- Browser automation/scraping: Playwright and/or Firecrawl (Firecrawl's hosted
  API absorbs proxy rotation/anti-bot handling for sites that need it).
- LLM extraction turns a scraped page into structured event fields
  (name/location/date/price/distance/age restriction/...); the specific LLM
  provider is swappable.

**2. Feed-import pipeline** — for sources that already publish a structured,
ready-to-use bulk feed instead of a site to crawl page-by-page (parkrun today;
Meetup and OpenStreetMap's Overpass API are candidates, not yet built):

```
┌─────────────┐   scheduler (cron, e.g. weekly)
│ Cloud       │──────────────┐
│ Scheduler   │              ▼
└─────────────┘      ┌────────────────────────┐
                     │ Worker: feed-import     │  fetch + parse feed
                     │  (no browser, no LLM)   │  resolve/create organiser
                     └────────┬────────────────┘  upsert events directly
                                ▼
                      ┌──────────────────┐
                      │ Database         │
                      └──────────────────┘
```

- One call does everything — fetch the feed, resolve or create the umbrella
  organiser the whole feed belongs to, upsert every event row — there's no
  separate "discover URLs, then fan out" stage the way the pattern-website
  pipeline has, so there's nothing to put on a per-item queue.
- No LLM call and no per-page robots.txt check, because there's no per-page
  fetch: the feed already gives structured fields directly.
- Cancellation state from a source feed (e.g. parkrun's own cancellations
  column) is mapped into the event's lifecycle status (scheduled / cancelled /
  postponed), so a feed re-run flips events back and forth as needed rather
  than only ever adding new rows.

Both pipelines write with `registrator` set to identify *what* produced a row
("bot" for an unattended crawl, or a named person for a site where crawling
was separately, explicitly authorised beyond what robots.txt alone allows) —
the same attribution mechanism the crowd-sourcing side uses for human edits,
so bot-sourced and human-sourced data share one provenance/history model.

### History & crowd-sourcing (cross-cutting, not a separate tier)

Applies uniformly to bot-written and user-written rows:

- Every create/update/soft-delete of an `organiser`/`event`/`event_occurrence`
  — whether from a crawler worker or a user edit via the backend API — bumps
  that row's `version` and writes one `contributions` row (who, when, what
  changed, why).
- Soft delete only (`deleted_at`/`deleted_by`); nothing is hard-deleted, so
  history is never destroyed.
- Mass-revert: given a user (or a bot run) that did damage, find every
  contribution they made, restore each affected entity to the version
  immediately before, and log the restore itself as a new contribution. The
  bad contributions stay in the log forever; they simply stop being the live
  version.
- Community verification (thumbs-up / "not there", weighted by contributor
  reputation, no single-user instant override) is a backend-API concern built
  on top of this log, not a separate data store.

## Technologies

Only technologies actually named (or clearly implied) in `README.md`:

| Concern | Technology |
|---|---|
| Mobile app | Flutter |
| Website | web frontend (framework unspecified in README) |
| Backend API (user-facing) | HTTP app server (framework unspecified in README) |
| Crawler task server | FastAPI (`/tasks/listing-crawl`, `/tasks/event-crawl`, `/tasks/feed-import`), on Cloud Run |
| Scheduling | Cloud Scheduler (cron) |
| Work queues | Pub/Sub-style queues (`listings`, `events`) for the pattern-website pipeline |
| Browser automation / scraping | Playwright and/or Firecrawl (self-hosted crawl4ai as the primary/free backend, Firecrawl's hosted API as the paid anti-bot/proxy fallback) |
| LLM extraction | pluggable provider — Grok or Anthropic (Claude) |
| Database | Postgres, hosted on Neon (serverless Postgres) — schema given in Postgres DDL (`serial`, `jsonb`, `timestamptz`); Firestore considered and parked |
| Payments | Stripe, paid direct to the organiser/customer (no commission held by the platform) |
| Cloud provider | Google Cloud for compute/scheduling/queues (Cloud Scheduler, Cloud Run, Pub/Sub); Neon for the database itself |
| Data sources | organiser sites (primary); aggregators (`findarace.com`, ...) and platforms — discovery of organisers only, never event data; parkrun (via a third-party, MIT-licensed GitHub-hosted feed, not parkrun.com directly — parkrun's own robots.txt disallows unattended crawling); Meetup and OpenStreetMap's Overpass API — proposed, unconfirmed as real sources |
