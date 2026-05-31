import logging
from llm_providers import PROVIDERS, call_provider

logger = logging.getLogger(__name__)


async def check_grammar(text: str) -> tuple[dict | None, str | None]:
    for provider in sorted(PROVIDERS, key=lambda p: p["priority"]):
        result = await call_provider(provider, text)
        if result is not None:
            return result, provider["name"]
    logger.error("❌ All providers failed or exhausted.")
    return None, None
