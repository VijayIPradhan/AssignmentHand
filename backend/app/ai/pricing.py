"""Model prices (USD per million tokens) and cost calculation.

Resolution order for a model id:
1. ``LLM_PRICING`` (JSON, e.g. ``{"global.anthropic.claude-opus-5-5": [4.0, 20.0]}``) - your actual rates.
2. Anthropic first-party list prices, matched after stripping cloud prefixes ("global.anthropic.", "us.",
   "anthropic." ...). For a cloud id this is labelled an *estimate*: Bedrock/Vertex pricing can differ.
3. Unknown: tokens are still recorded, cost is left empty.
"""

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from app.config import get_settings

log = logging.getLogger(__name__)

# Anthropic API list prices (input, output) per million tokens.
LIST_PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}

_CLOUD_PREFIX = re.compile(r"^(?:(?:global|us|eu|apac|in|jp|au)\.)?(?:anthropic\.)?")
_VERSION_SUFFIX = re.compile(r"(-\d{8})?(-v\d+(:\d+)?)?$")


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float
    source: str  # configured | list_price | list_price_estimate


@lru_cache
def _configured() -> dict[str, tuple[float, float]]:
    raw = get_settings().llm_pricing
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {k: (float(v[0]), float(v[1])) for k, v in data.items()}
    except (ValueError, TypeError, IndexError, KeyError):
        log.warning("LLM_PRICING is not valid JSON of the form {model: [input, output]}; ignored")
        return {}


def base_model(model: str) -> str:
    """'global.anthropic.claude-opus-5-5' -> 'claude-opus-5-5' (also strips date/version suffixes)."""
    return _VERSION_SUFFIX.sub("", _CLOUD_PREFIX.sub("", model or ""))


def price_for(model: str) -> Price | None:
    if not model:
        return None
    conf = _configured()
    if model in conf:
        return Price(*conf[model], "configured")
    base = base_model(model)
    if base in conf:
        return Price(*conf[base], "configured")
    if base in LIST_PRICES:
        return Price(*LIST_PRICES[base], "list_price" if base == model else "list_price_estimate")
    return None


def cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    p = price_for(model)
    if p is None:
        return None
    return round(input_tokens / 1e6 * p.input_per_mtok + output_tokens / 1e6 * p.output_per_mtok, 6)


def estimate_tokens(chars: int) -> int:
    return max(1, int(chars / 3.5))
