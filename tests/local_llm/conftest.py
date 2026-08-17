"""
Shared setup for tests that exercise a real local LLM (Ollama) - see
llm_extractor.py's module docstring for why this provider exists at all
(tests only, never the real pipeline).

Confirmed in practice: two calls against qwen2.5:7b on a dev machine with
Ollama already running took over 10 minutes total - vs. ~17 seconds for the
*entire* rest of the suite. Skipped unless RUN_LOCAL_LLM_TESTS=1 is set,
regardless of whether Ollama happens to be reachable - "Ollama is running" is
true on plenty of dev machines and must never be what silently turns a 17s
`pytest` into a 10-minute one. Opt in explicitly:

    RUN_LOCAL_LLM_TESTS=1 poetry run pytest tests/local_llm/

Also skipped (independent of the env var) if Ollama isn't reachable or the
configured model isn't pulled, so an explicit opt-in on a machine without
Ollama installed still fails soft rather than erroring. Unlike the rest of the
suite, this directory is the one deliberate exception to "no real network/LLM
calls anywhere" (see conftest.py at the tests/ root) - gating behind both the
env var and a reachability check keeps that exception opt-in and contained.
"""

import os

import pytest

from services.config import settings

_ENV_VAR = "RUN_LOCAL_LLM_TESTS"


def _ollama_ready() -> tuple[bool, str]:
    try:
        import ollama
    except ImportError:
        return False, "the ollama package is not installed"

    try:
        client = ollama.Client(host=settings.local_llm_base_url)
        models = {m.model for m in client.list().models}
    except Exception as e:
        return False, f"Ollama not reachable at {settings.local_llm_base_url}: {type(e).__name__}: {e}"

    # Ollama's own model names carry an explicit ":latest" tag that a bare
    # "qwen2.5:7b"-style config value doesn't - compare against both forms
    # rather than requiring the user to spell out the tag in settings.
    wanted = settings.local_llm_model
    if wanted in models or f"{wanted}:latest" in models:
        return True, ""
    return False, f"model {wanted!r} is not pulled in Ollama (have: {sorted(models)})"


@pytest.fixture(autouse=True)
def _require_ollama(monkeypatch):
    if os.environ.get(_ENV_VAR) != "1":
        pytest.skip(
            f"local-LLM tests are opt-in (slow: ~5+ min per call) - set {_ENV_VAR}=1 to run them"
        )

    ready, reason = _ollama_ready()
    if not ready:
        pytest.skip(f"local Ollama unavailable, skipping: {reason}")
    # Every test in this directory runs the real extraction path against the
    # real local model - override whatever LLM_PROVIDER is configured for the
    # rest of the suite (grok, by conftest.py's own default expectations).
    monkeypatch.setattr(settings, "llm_provider", "local")
