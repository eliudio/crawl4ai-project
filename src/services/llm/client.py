"""
Provider plumbing shared by every LLM-backed extraction task in this package.

Provider is chosen via LLM_PROVIDER ("grok", "anthropic", or "local") so any
task can be swapped without touching callers - see common/config.py. All
three are asked to fill the same schema so callers never need to care which
one answered.

"local" (a self-hosted Ollama model) is not used by the real pipeline - a
self-hosted GPU's fixed hourly cost loses to Grok's/Anthropic's per-token
pricing at this project's actual crawl volume, and cloud deployment makes that
worse, not better. It exists purely so tests/llm/local/ can exercise these
real prompts against a real model - catching a prompt/schema regression that a
canned-response mock never could - without paying for or depending on a
hosted API.
"""

import json
from typing import Any

from services.common.config import settings

__all__: list[str] = []  # internal to the llm package - see event_extraction.py/listing_extraction.py


def _build_user_prompt(instructions: str, schema_properties: dict[str, Any], required: list[str]) -> str:
    schema = {"type": "object", "properties": schema_properties, "required": required}
    return f"{instructions}\n\nJSON schema:\n{json.dumps(schema, indent=2)}"


def _call_grok(system_prompt: str, user_prompt: str, max_tokens: int) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=settings.grok_api_key, base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model=settings.grok_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _call_anthropic(
    system_prompt: str,
    user_prompt: str,
    schema_properties: dict[str, Any],
    required: list[str],
    tool_name: str,
    max_tokens: int,
) -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    tool = {
        "name": tool_name,
        "description": "Record the requested structured output.",
        "input_schema": {"type": "object", "properties": schema_properties, "required": required},
    }
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system_prompt,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": user_prompt}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Anthropic response did not include a tool_use block")


def _call_local(system_prompt: str, user_prompt: str, max_tokens: int) -> dict[str, Any]:
    """
    A self-hosted Ollama model - see this module's own docstring for why this exists
    (tests/llm/local/ only, never the real pipeline). format="json" is Ollama's
    unconstrained JSON mode (unlike Anthropic's tool-forced schema or Grok's
    response_format), so a small/quantized model can still occasionally return
    malformed or off-schema JSON - callers already tolerate that via the same
    try/except every other provider goes through in extract_event_fields et al.

    num_ctx: explicit, not left at Ollama's own runtime default - confirmed in practice
    to matter, not just theoretical: extract_event_fields's own schema (every field's
    full description, dumped as literal JSON text into the prompt - see
    _build_user_prompt) is now ~4000 tokens on its own after the occurrence/
    registration/lifecycle fields piled up, and Ollama's runtime default num_ctx is
    smaller than that regardless of a model's own much larger supported context length
    (qwen2.5:7b supports 32768) - silently truncating/starving the model of context for
    it, not a prompt-wording problem. Reproduced directly: test_real_event_page_extracts_
    sane_fields (a trivial, previously-reliable 2-distance fixture) started coming back
    with an empty `distances` array, twice in a row, purely from the schema having grown -
    nothing about that test's own markdown or assertions changed. 8192 comfortably covers
    today's schema + a real event page's markdown + generation, with headroom to keep
    growing before this has to be revisited.
    """
    import ollama

    client = ollama.Client(host=settings.local_llm_base_url)
    response = client.chat(
        model=settings.local_llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        format="json",
        options={"temperature": 0.0, "num_predict": max_tokens, "num_ctx": 8192},
    )
    return json.loads(response["message"]["content"])


def _run_llm(
    system_prompt: str,
    user_prompt: str,
    schema_properties: dict[str, Any],
    required: list[str],
    tool_name: str,
    max_tokens: int = 1200,
) -> dict[str, Any]:
    if settings.llm_provider == "anthropic":
        return _call_anthropic(system_prompt, user_prompt, schema_properties, required, tool_name, max_tokens)
    if settings.llm_provider == "local":
        return _call_local(system_prompt, user_prompt, max_tokens)
    return _call_grok(system_prompt, user_prompt, max_tokens)
