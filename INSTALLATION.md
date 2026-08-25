# Installation — running this in the cloud

## 0. GCP project

1. `gcloud auth login` — opens a browser, authenticates the CLI as you.
2. Create the project (id must be globally unique, 6–30 lowercase
   letters/digits/hyphens):
   ```
   gcloud projects create <project-id> --name="<display name>"
   ```
3. **Link a billing account** — Cloud Run/Pub/Sub/Scheduler all require
   billing enabled on the project, even though each has a free tier that
   likely covers this project's actual usage:
   ```
   gcloud billing accounts list                                    # find the account id
   gcloud billing projects link <project-id> --billing-account=<account-id>
   ```
   (No billing account yet: create one at
   [console.cloud.google.com/billing](https://console.cloud.google.com/billing) first —
   needs a payment method, it's not optional even for the free tier.)
4. Make it the active project for every `gcloud` command below:
   ```
   gcloud config set project <project-id>
   ```

## 1. Database

→ [Neon](https://neon.tech) — create a project, copy the connection string
details into `.env`.

1. Sign up / log in at neon.tech, **New project** (pick a region close to
   where you'll run Cloud Run — same-region keeps latency down).
2. Open the project's **Connect** panel and copy the **pooled** connection
   string (host has a `-pooler` suffix) — this is what both services below
   use day to day.
3. Also copy the **unpooled** connection string (same host, minus
   `-pooler`) — only used for schema migrations (`ALTER TABLE` doesn't
   reliably work through Neon's transaction-mode pooler).
4. `cp src/services/.env.example src/services/.env` and fill in:
   ```
   DATABASE_URL=postgresql+psycopg://<user>:<password>@<ep-xxx>-pooler.<region>.aws.neon.tech/<dbname>?sslmode=require
   DATABASE_URL_UNPOOLED=postgresql+psycopg://<user>:<password>@<ep-xxx>.<region>.aws.neon.tech/<dbname>?sslmode=require
   ```
   (`sslmode=require` is mandatory — Neon rejects plaintext connections.)
5. Create the schema once, from your machine, against the *unpooled* URL:
   ```
   cd src
   DATABASE_URL=$DATABASE_URL_UNPOOLED poetry run python -c "from services.common import init_db; init_db()"
   ```
   (`init_db()` also runs on every deploy indirectly — see step 6 below — but
   running it once up front lets you confirm the connection works before
   anything else depends on it.)
6. A Neon **branch** (Neon's copy-on-write DB branching, not a Git branch) is
   the cheapest way to get an isolated database per environment (e.g. a
   `staging` branch for a pre-prod Cloud Run revision) — create one from the
   Neon dashboard/CLI and point that environment's `DATABASE_URL`/
   `DATABASE_URL_UNPOOLED` at the branch's own connection strings instead.

## 2. Enable APIs & Artifact Registry

Enable the APIs both services need (on the project set in step 0):

```
gcloud services enable run.googleapis.com pubsub.googleapis.com \
    cloudscheduler.googleapis.com artifactregistry.googleapis.com
```

Create an Artifact Registry repo to push images to (once per project):

```
gcloud artifacts repositories create crawler --repository-format=docker --location=<region>
```

## 3. Secrets

Everything both services read comes from environment variables (see
`common/config.py`) — for anything beyond the database URL (LLM API keys,
Firecrawl key), store them in Secret Manager rather than passing them as
plain `--set-env-vars`:

```
printf '%s' "$DATABASE_URL" | gcloud secrets create database-url --data-file=-
printf '%s' "$GROK_API_KEY" | gcloud secrets create grok-api-key --data-file=-
# ...repeat for ANTHROPIC_API_KEY / FIRECRAWL_API_KEY if you use them
```

Each `gcloud run deploy` below references these via `--set-secrets`.

## 4. Pub/Sub queues (crawler pipeline)

```
gcloud pubsub topics create listing-crawl event-crawl feed-import
```

## 5. Crawler pipeline service

This is `src/services/Dockerfile` — the pattern-website + feed-import
pipelines' task server (`services.server.main:app`). It needs a browser
(Playwright/Chromium), so it's the bigger of the two images.

```
docker build -f src/services/Dockerfile -t <region>-docker.pkg.dev/<project>/crawler/pipeline .
docker push <region>-docker.pkg.dev/<project>/crawler/pipeline

gcloud run deploy crawler-pipeline \
    --image <region>-docker.pkg.dev/<project>/crawler/pipeline \
    --region <region> \
    --no-allow-unauthenticated \
    --set-secrets DATABASE_URL=database-url:latest,GROK_API_KEY=grok-api-key:latest \
    --set-env-vars GCP_PROJECT_ID=<project>
```

`--no-allow-unauthenticated`: this service only ever receives Pub/Sub push
deliveries, never a browser — grant the push subscription's service account
the `roles/run.invoker` role rather than opening it to the public:

```
gcloud pubsub topics create listing-crawl event-crawl feed-import   # if not already done in step 4

gcloud pubsub subscriptions create listing-crawl-push --topic listing-crawl \
    --push-endpoint <run-url>/tasks/listing-crawl --push-auth-service-account <invoker-sa>
gcloud pubsub subscriptions create event-crawl-push --topic event-crawl \
    --push-endpoint <run-url>/tasks/event-crawl --push-auth-service-account <invoker-sa>
gcloud pubsub subscriptions create feed-import-push --topic feed-import \
    --push-endpoint <run-url>/tasks/feed-import --push-auth-service-account <invoker-sa>

gcloud run services add-iam-policy-binding crawler-pipeline \
    --region <region> --member=serviceAccount:<invoker-sa> --role=roles/run.invoker
```

Seed the initial organiser list and kick off the first crawl:

```
cd src
poetry run python -m services.admin.seed_organisers --publish
```

Recurring triggers (Cloud Scheduler → Pub/Sub):

```
gcloud scheduler jobs create pubsub recrawl-organisers --schedule="0 3 * * *" \
    --topic=listing-crawl --message-body='...'          # nightly per-organiser recrawl
gcloud scheduler jobs create pubsub feed-import-parkrun --schedule="0 4 * * 1" \
    --topic=feed-import --message-body='{"source": "parkrun", "params": {}}'   # weekly
```

## 6. Admin web interface

This is `src/services/Dockerfile.admin` — the on-the-fly DB browser
(`services.admin.web.app:app`, see `ARCHITECTURE.md`'s "Admin interface"
section). It never scrapes or calls an LLM, so it's a much smaller image than
the crawler pipeline's (no Chromium). It is **not** meant to be public: every
page is a raw view over the full database, including internal-only fields
like `invalid_reason`.

```
docker build -f src/services/Dockerfile.admin -t <region>-docker.pkg.dev/<project>/crawler/admin-web .
docker push <region>-docker.pkg.dev/<project>/crawler/admin-web

gcloud run deploy admin-web \
    --image <region>-docker.pkg.dev/<project>/crawler/admin-web \
    --region <region> \
    --no-allow-unauthenticated \
    --set-secrets DATABASE_URL=database-url:latest \
    --set-secrets ADMIN_BASIC_AUTH_USER=admin-basic-auth-user:latest,ADMIN_BASIC_AUTH_PASSWORD=admin-basic-auth-password:latest
```

Two layers of access control, deliberately not just one:

1. **Primary**: `--no-allow-unauthenticated` restricts *invocation* to
   principals with `roles/run.invoker` — grant it to yourself/maintainers
   only:
   ```
   gcloud run services add-iam-policy-binding admin-web \
       --region <region> --member=user:you@example.com --role=roles/run.invoker
   ```
   Reach it through `gcloud run services proxy admin-web --region <region>`
   (tunnels to localhost with your `gcloud` credentials attached) rather than
   opening the service's URL directly.
2. **Secondary**: the optional HTTP Basic gate (`ADMIN_BASIC_AUTH_USER`/
   `ADMIN_BASIC_AUTH_PASSWORD`, only enforced when both are set — see
   `common/config.py` and `admin/web/app.py`). Useful if you ever do put this
   behind a plain HTTPS load balancer instead of Cloud Run IAM, but it's a
   password, not a replacement for restricting who can even reach the
   service.

`/healthz` is intentionally exempt from both — Cloud Run's own liveness probe
hits it directly.

## 7. Verify

```
curl <crawler-run-url>/healthz     # {"status": "ok"}
gcloud run services proxy admin-web --region <region>   # then open http://localhost:8080/ in a browser
```
