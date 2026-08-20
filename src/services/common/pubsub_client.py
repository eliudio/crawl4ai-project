"""
Publish helper for the pipeline's work queues: listing-crawl/event-crawl (the
pattern-website pipeline's own per-organiser/per-event fan-out - see
pattern_site/listing_crawler.py/event_crawler.py) and feed-import (the separate,
scheduled structured-bulk-feed pipeline - see feeds/feed_importers.py), which is
dispatched by named source rather than fanned out per item.
"""

import json
from functools import lru_cache

from google.cloud import pubsub_v1

from .config import settings


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


def publish_feed_import(source: str, params: dict | None = None) -> None:
    """
    Ask the feed-import pipeline to run one named importer (e.g. "parkrun") - see
    feeds/feed_importers.py's registry and server/main.py's own /tasks/feed-import
    handler, the other end of this. Meant to be triggered on a schedule (Cloud
    Scheduler -> this topic) rather than per-organiser/per-event like the two functions
    above; params is forwarded to the importer as-is (e.g. feeds/parkrun_import.py's
    own "country" override).
    """
    publish(settings.feed_import_topic, {"source": source, "params": params or {}})
