"""Publish helper for the two work queues (listing-crawl, event-crawl)."""

import json
from functools import lru_cache

from google.cloud import pubsub_v1

from services.config import settings


@lru_cache(maxsize=1)
def _publisher() -> pubsub_v1.PublisherClient:
    return pubsub_v1.PublisherClient()


def publish(topic_name: str, data: dict) -> None:
    topic_path = _publisher().topic_path(settings.gcp_project_id, topic_name)
    payload = json.dumps(data).encode("utf-8")
    future = _publisher().publish(topic_path, payload)
    future.result(timeout=30)


def publish_listing_crawl(organiser_id: int) -> None:
    publish(settings.listing_crawl_topic, {"organiser_id": organiser_id})


def publish_event_crawl(organiser_id: int, event_url: str) -> None:
    publish(settings.event_crawl_topic, {"organiser_id": organiser_id, "event_url": event_url})
