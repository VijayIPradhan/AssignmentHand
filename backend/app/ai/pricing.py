"""Token cost estimates. Prices are USD per million tokens (first-party API list prices)."""

from app.config import get_settings

PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


def price_for(model: str) -> tuple[float, float] | None:
    s = get_settings()
    if s.llm_input_price_per_mtok is not None and s.llm_output_price_per_mtok is not None:
        return s.llm_input_price_per_mtok, s.llm_output_price_per_mtok
    return PRICES.get(model)


def cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    p = price_for(model)
    if p is None:
        return None
    return round(input_tokens / 1e6 * p[0] + output_tokens / 1e6 * p[1], 6)


def estimate_tokens(chars: int) -> int:
    return max(1, int(chars / 3.5))
