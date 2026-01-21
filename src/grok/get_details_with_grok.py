# Run this first if needed:
# pip install --upgrade firecrawl-py

from firecrawl import FirecrawlApp

from datetime import datetime
from openai import OpenAI
import json
from typing import Dict, Any

from events.events_manager import event_from_dict, insert_or_skip_events, event_with_url_exists


def get_details_with_grok(url: str, markdown: str) -> Dict[str, Any] | None:
    """
    Extracts event name, date and location using Grok API.
    Returns a dictionary with the three fields (or error info).
    """
    print(f"{datetime.now():%H:%M:%S} - get_details_with_grok: {url}")
    # ── You fill these in once you have them ──
    GROK_API_KEY = "***REDACTED-ROTATE-THIS-KEY***"   # ← put your real key here

    client = OpenAI(
        api_key=GROK_API_KEY,
        base_url="https://api.x.ai/v1",           # important – this is Grok's endpoint
    )

    schema = {
        "type": "object",
        "properties": {
            "event_name": {"type": "string", "description": "Full event name/title"},
            "date": {"type": "string", "description": "Date or date range, exact as written"},
            "location": {
                "type": "string",
                "description": "Location including city, venue, country, postcode/zip if present - be as complete as possible. The page might have multiple occurrences of location. Ideally is postcode. Second is the full address. If not, be as complete as possible. "
            },
            "start": {
                "type": "string",
                "description": "Location where the race starts, if provided"
            },
            "event_summary": {
                "type": "string",
                "description": "Rephrased, summary of the description or summary available"
            },
            "organiser": {
                "type": "string",
                "description": "The organiser organising the event"
            },
            "finish": {
                "type": "string",
                "description": "Location where the race finishes, if provided"
            },
        },
        "required": ["event_name", "date", "location"],
        "additionalProperties": False
    }

    system_prompt = (
        "You are a precise event data extractor. "
        "Return ONLY valid JSON matching the schema below. "
        "No explanations, no extra text, no markdown. "
        "If a field is missing or unclear → use \"Not found\"."
    )

    user_prompt = (
        f"Extract from this markdown:\n\n{markdown}\n\n"
        f"JSON schema to follow:\n{json.dumps(schema, indent=2)}"
    )

    try:
        response = client.chat.completions.create(
            model="grok-4-1-fast-reasoning",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=300,                         # plenty for this task
            response_format={"type": "json_object"} # very important – forces JSON
        )

        extracted_str = response.choices[0].message.content
        parsed = json.loads(extracted_str)

        parsed["url"] = url
        parsed["md"] = markdown
        return parsed

    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} - Extraction failed:", str(e))
        return None