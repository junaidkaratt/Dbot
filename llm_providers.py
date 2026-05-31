"""
llm_providers.py — Generic LLM provider system.

HOW TO ADD A NEW PROVIDER:
1. Install its SDK in requirements.txt (if needed)
2. Add an entry to the PROVIDERS list with:
   - name: display name
   - priority: order to try (1 = first)
   - env_key: environment variable name for the API key
   - call: a dict describing how to call it (see existing examples)

Supported call types:
  - "openai_compat": any OpenAI-compatible API (OpenAI, Groq, Mistral, OpenRouter, Together, etc.)
  - "gemini": Google Gemini via google-generativeai SDK
  - "cohere": Cohere via cohere SDK
"""

import os
import json
import logging
import httpx

logger = logging.getLogger(__name__)

GRAMMAR_PROMPT = lambda text: f"""You are a grammar correction assistant. Analyze the following text for grammar, spelling, and punctuation errors.

Text: "{text}"

Respond ONLY in this exact JSON format with no extra text or markdown:
{{
  "is_correct": true or false,
  "corrected_text": "the corrected version (same as original if already correct)",
  "explanation": "brief explanation of what was fixed, or 'No errors found.' if correct"
}}"""


def parse_llm_json(raw: str) -> dict | None:
    try:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception:
        return None


async def call_provider(provider: dict, text: str) -> dict | None:
    """
    Single generic function that calls any LLM provider based on its config.
    """
    api_key = os.environ.get(provider["env_key"])
    if not api_key:
        logger.warning(f"⏭️  {provider['name']}: no API key set ({provider['env_key']})")
        return None

    call = provider["call"]
    prompt = GRAMMAR_PROMPT(text)

    try:
        # ── OpenAI-compatible (OpenAI, Groq, Mistral, OpenRouter, Together, etc.) ──
        if call["type"] == "openai_compat":
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    call["base_url"] + "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        **call.get("extra_headers", {}),
                    },
                    json={
                        "model": call["model"],
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 500,
                        "temperature": 0,
                    },
                    timeout=15,
                )
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"]

        # ── Gemini (uses its own SDK) ──
        elif call["type"] == "gemini":
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(call["model"])
            resp = model.generate_content(prompt)
            raw = resp.text

        # ── Cohere (uses its own SDK) ──
        elif call["type"] == "cohere":
            import cohere
            co = cohere.AsyncClient(api_key=api_key)
            resp = await co.chat(model=call["model"], message=prompt)
            raw = resp.text

        else:
            logger.error(f"Unknown call type: {call['type']}")
            return None

        result = parse_llm_json(raw)
        if result:
            logger.info(f"✅ Used provider: {provider['name']} ({call['model']})")
        return result

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status in (429, 503):
            logger.warning(f"⏭️  {provider['name']}: rate limited ({status})")
        else:
            logger.warning(f"⏭️  {provider['name']}: HTTP {status}")
        return None
    except Exception as e:
        logger.warning(f"⏭️  {provider['name']} failed: {e}")
        return None


# ── PROVIDERS LIST ─────────────────────────────────────────────────────────────
# Add any LLM here. Only providers with a valid env_key set will be tried.
# OpenAI-compatible providers just need base_url + model — no new code needed.

PROVIDERS = [
    {
        "name": "Gemini",
        "priority": 1,
        "env_key": "GEMINI_API_KEY",
        "call": {"type": "gemini", "model": "gemini-2.0-flash"},
    },
    {
        "name": "Groq",
        "priority": 2,
        "env_key": "GROQ_API_KEY",
        "call": {"type": "openai_compat", "base_url": "https://api.groq.com/openai/v1", "model": "llama3-8b-8192"},
    },
    {
        "name": "OpenRouter",
        "priority": 3,
        "env_key": "OPENROUTER_API_KEY",
        "call": {"type": "openai_compat", "base_url": "https://openrouter.ai/api/v1", "model": "meta-llama/llama-3-8b-instruct:free"},
    },
    {
        "name": "Mistral",
        "priority": 4,
        "env_key": "MISTRAL_API_KEY",
        "call": {"type": "openai_compat", "base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest"},
    },
    {
        "name": "OpenAI",
        "priority": 5,
        "env_key": "OPENAI_API_KEY",
        "call": {"type": "openai_compat", "base_url": "https://api.openai.com/v1", "model": "gpt-3.5-turbo"},
    },
    {
        "name": "Together AI",
        "priority": 6,
        "env_key": "TOGETHER_API_KEY",
        "call": {"type": "openai_compat", "base_url": "https://api.together.xyz/v1", "model": "mistralai/Mistral-7B-Instruct-v0.2"},
    },
    {
        "name": "Cohere",
        "priority": 7,
        "env_key": "COHERE_API_KEY",
        "call": {"type": "cohere", "model": "command-r"},
    },
]