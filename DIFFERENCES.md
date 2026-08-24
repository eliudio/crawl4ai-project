# Differences: current code vs. `ARCHITECTURE.md`

What needs to change or be built to close the gap between the current code
(`src/services`, `restructure-services` branch) and `ARCHITECTURE.md`. This
only lists outstanding work — anything the code already does correctly per
`ARCHITECTURE.md` is not repeated here.

Two kinds of gap, kept separate:

- **Impact of the new architecture** — structural/infrastructure changes
  needed to *fit the shape* of the new architecture (new tiers, new hosting
  technology, foundational schema), independent of any one feature.
- **Todo's from the functionality** — product features `README.md` describes
  that still need building on top of that structure.

## Impact of new architecture

### A1. A backend API tier needs to be created; it doesn't exist yet

`ARCHITECTURE.md`'s backend API tier (search/map, auth, crowd-sourced
edits, booking) has no code today. `src/services/server/main.py` is only the
crawler tier's internal task API (`/healthz`, `/tasks/listing-crawl`,
`/tasks/event-crawl`, `/tasks/feed-import`), meant to sit behind Pub/Sub push
subscriptions — it has no search/read endpoint, no auth, and isn't meant to
be exposed to end users.

**To do:** stand up a new, separately deployable service (e.g.
`src/services/api/`) for this tier rather than extending `server/main.py` —
the two have different audiences, auth models, and scaling profiles. This is
a prerequisite for every user-facing item in the "functionality" section
below (community edits, booking, moderation all need somewhere to live).

### A2. Adopt Neon as the Postgres provider

`ARCHITECTURE.md` now specifies Neon (serverless Postgres) as the database.
The code currently documents and defaults to a generic/self-hosted-style
Postgres URL:

- `common/config.py`'s `database_url` setting is documented as *"Postgres
  (Cloud SQL in production)"* with a plain local-Postgres example
  (`common/config.py:22-25`) — needs updating to Neon, including:
  - Using Neon's pooled connection endpoint (PgBouncer-compatible) for the
    crawler workers and any future API tier, since both are bursty/
    many-short-lived-connections workloads — exactly what Neon's pooler is
    for, and what `ARCHITECTURE.md` now calls out.
  - `sslmode=require` (Neon requires TLS) in the example connection string.
  - Deciding whether admin/export scripts (`admin/export/cli.py`) and
    `common/db.py`'s `_add_missing_columns()` DDL migrations need the
    *unpooled* Neon connection string instead (session-mode operations like
    `ALTER TABLE` don't always play well through a transaction-mode pooler) —
    if so, add a second settings field (e.g. `database_url_unpooled`) rather
    than overloading one URL for both uses.
- No deployment docs/config in the repo yet reference Neon at all (project
  creation, branch-per-environment strategy, where the connection string(s)
  come from at deploy time) — needs adding wherever Cloud Run's own env vars
  are documented/set.

### A3. Add the attribution/versioning schema the crowd-sourcing tier depends on

`ARCHITECTURE.md`'s cross-cutting "History & crowd-sourcing" behaviour
(every write versioned and attributed, soft-delete only, mass-revert by
replaying the contribution log) has no data-layer support yet in
`common/models/`:

- No `users` table.
- No `contributions` or `changesets` tables.
- No `created_by` / `updated_by` / `version` / `deleted_at` / `deleted_by`
  columns on `Organiser` (`common/models/organiser.py`) or on `Event` /
  `EventOccurrence` (`common/models/event.py`).

**To do:** add `common/models/user.py` (`users`) and
`common/models/contribution.py` (`contributions`, `changesets`), plus the
attribution/versioning columns on `organiser.py` and `event.py`. Give
`version`/`created_by` etc. server-side defaults so they stay auto-migratable
through `common/db.py`'s existing `_add_missing_columns()` mechanism, the
same way `Event.occurrence`/`registration_status` already are. This is a
prerequisite for every history/moderation item in the functionality section
below — none of that can be built without this schema existing first.

## Todo's from the functionality

### F1. Price as a comparable value, not free text

`EventDistance.price_text` and `EventOccurrence.price_text`
(`common/models/event.py:208`, `:257`) are free text only — no numeric amount,
no currency code, so cross-listing price comparison isn't possible.

**To do:** add `price_amount` (numeric) and `price_currency` columns
alongside the existing verbatim `price_text` on both tables in
`common/models/event.py`, and have `llm/event_extraction.py` attempt to
parse/normalise a value + ISO currency code in addition to the always-present
verbatim text.

### F2. Provenance note on bot-created rows

`Organiser.discovered_via` (`common/models/organiser.py:77`) records how an
*organiser* was discovered, but there's no equivalent free-text "retrieved
from url ..." note for `Event`/`EventOccurrence`, and no general-purpose note
usable by any bot-written row.

**To do:** once A3's `contributions` table exists, have every bot write log
its source note there (`contributions.comment`) the same way a human edit
summary would, rather than adding a second, parallel notes column.

### F3. Map parkrun's cancellation feed column into `lifecycle_status`

`feeds/parkrun_import.py` hardcodes every imported row to
`"lifecycle_status": "scheduled", "lifecycle_text": None`
(`parkrun_import.py:139-140`), regardless of the source TSV's own
`Cancellations` column — so nothing imported from parkrun is ever marked
cancelled/postponed, even though `Event.lifecycle_status`/`lifecycle_text`
and the `EventLifecycle` enum already exist to receive it
(`common/models/event.py:95-108`, `common/models/enums.py`).

**To do:** in `feeds/parkrun_import.py`, read the row's `Cancellations`
column and set `lifecycle_status`/`lifecycle_text` from it instead of the
current hardcoded scheduled state (including un-cancelling an event that
previously was flagged cancelled but no longer appears in the feed's
cancellation list). Note `README.md` flags the column's real shape/values as
still unconfirmed (every sampled row so far was empty `"[]"`) — confirm
against a row that actually has a value before mapping it.

### F4. Automate aggregator → organiser discovery

`admin/seed_organisers.py` only loads a hand-maintained
`data/organisers_seed.csv`; there is no crawler that walks an aggregator/
platform site and derives new `Organiser` rows from it automatically.

**To do:** a new module (e.g. `pattern_site/aggregator_discovery.py`) that
crawls aggregator/platform sites, resolves each listing to its real organiser
homepage, and inserts `Organiser` rows accordingly — using the existing
`Organiser.source_type` enforcement point (already relied on by
`pattern_site/listing_crawler.py`) to guarantee only the resolved organiser's
own site, never the aggregator's, is ever fed into event crawling.

### F5. Meetup / OpenStreetMap feed importers

`feeds/` only contains `feed_importers.py` (the registry) and
`parkrun_import.py`. No importer exists for Meetup or OpenStreetMap's
Overpass API.

**To do:** `feeds/meetup_import.py` and/or `feeds/osm_import.py`, registered
into `feed_importers.py`'s existing name→callable registry alongside
`parkrun_import.py` — no changes needed to `server/main.py`'s
`/tasks/feed-import` dispatch, the pipeline shape already generalizes to
this.

### F6. Booking system + Stripe

No code anywhere touches payments or bookings (the only `stripe`/`booking`/
`payment` hits in the codebase are comments describing *other sites'*
booking platforms encountered while scraping, e.g. `common/models/event.py`,
`scraping/backends/crawl4ai_client.py` — nothing owned by this project).

**To do:** depends on A1 (backend API tier). Needs new DB tables for real
bookings/seats/payments, distinct from the scraped `EventOccurrence`/
`EventDistance` rows (which represent *listing* data read from an
organiser's own site, not a booking record), plus Stripe integration paying
out direct to the organiser/customer with no commission held by the
platform.

### F7. Community moderation layer

No code implements reputation/levels, thumbs-up/"not there" verification,
de-duplication of near-identical reports, abuse detection, or
ghosting/shadow-banning.

**To do:** depends on A1 (backend API tier) and A3 (`contributions`/`users`
schema). Lives entirely in the new backend API tier once both exist:
reputation scoring and verification logic read/write the `contributions` log
and `users` table from A3.
