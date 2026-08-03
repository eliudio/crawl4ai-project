"""
Cloud Run HTTP entrypoint. Pub/Sub push subscriptions deliver messages here
as HTTP POSTs; each handler decodes the message, does the work, and returns
2xx to ack (Pub/Sub retries automatically on any other status).

Run locally with: uvicorn services.main:app --reload
"""

import base64
import json
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request

from services import event_crawler, listing_crawler, pubsub_client
from services.db import session_scope
from services.models import Organiser, SourceType

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def _decode_push_message(body: dict) -> dict:
    try:
        data = body["message"]["data"]
    except KeyError:
        raise HTTPException(status_code=400, detail="not a Pub/Sub push envelope")
    return json.loads(base64.b64decode(data))


@app.post("/tasks/listing-crawl")
async def handle_listing_crawl(request: Request):
    payload = _decode_push_message(await request.json())
    organiser_id = payload["organiser_id"]

    with session_scope() as session:
        organiser = session.get(Organiser, organiser_id)
        if organiser is None or not organiser.active:
            return {"status": "skipped", "reason": "organiser missing or inactive"}
        if organiser.source_type != SourceType.ORGANISER:
            # Belt-and-braces: only organiser rows should ever reach this queue.
            return {"status": "skipped", "reason": "source_type is not organiser"}

        new_urls = listing_crawler.crawl_listing(session, organiser)

    for url in new_urls:
        pubsub_client.publish_event_crawl(organiser_id, url)

    print(f"{datetime.now():%H:%M:%S} - listing-crawl {organiser.homepage_url}: enqueued {len(new_urls)} event(s)")
    return {"status": "ok", "enqueued": len(new_urls)}


@app.post("/tasks/event-crawl")
async def handle_event_crawl(request: Request):
    payload = _decode_push_message(await request.json())
    organiser_id = payload["organiser_id"]
    event_url = payload["event_url"]

    with session_scope() as session:
        organiser = session.get(Organiser, organiser_id)
        if organiser is None or not organiser.active or organiser.source_type != SourceType.ORGANISER:
            return {"status": "skipped", "reason": "organiser missing, inactive, or not source_type=organiser"}

        event = event_crawler.crawl_event(session, organiser_id, event_url)

    return {"status": "ok" if event else "failed"}
