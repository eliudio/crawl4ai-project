# Running locally

## setup locally

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

### run locally

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

## Deploying (GCP)

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


