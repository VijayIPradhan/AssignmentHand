"""Generate and validate one answer."""

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from app.ai.pricing import cost
from app.ai.prompts import ANSWER_SCHEMA, SYSTEM_PROMPT, AnswerPreferences, build_user_prompt, min_words, target_words
from app.ai.providers import LLMInvalidOutput, LLMProvider
from app.render.markup import word_count

log = logging.getLogger(__name__)

MAX_INVALID_RETRIES = 1


class _Flag(BaseModel):
    type: str
    message: str = Field(max_length=2000)


class _AnswerOut(BaseModel):
    answer: str = Field(max_length=60_000)
    flags: list[_Flag] = Field(default_factory=list, max_length=20)
    confidence: str = "medium"


@dataclass
class AnswerOutcome:
    content: str
    flags: list[dict]
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    word_count: int


@dataclass
class QuestionSpec:
    number: str
    text: str
    marks: float | None
    context: str | None
    instructions: str | None
    figures: list[bytes]
    max_words: int | None = None  # hard limit, e.g. the space available on the answer sheet


_HARD_WORDS = re.compile(
    r"\b(calculate|compute|derive|derivation|prove|proof|solve|evaluate the integral|differentiate|integrate|"
    r"equation|formula|numerical|algorithm|pseudo-?code|design an?|critically (evaluate|analy[sz]e))\b",
    re.I,
)
_MATH = re.compile(r"\$|\\frac|\\sqrt|[=+\-*/^]\s*\d|\d\s*[=+*/^]|[∫∑√π≤≥≠]")


def is_hard_question(prefs: AnswerPreferences, q: QuestionSpec) -> tuple[bool, str | None]:
    """Questions worth the strongest model: figures, maths/calculation, high marks, advanced depth.

    Returns (hard, reason) - the reason is logged so routing decisions can be reviewed."""
    text = f"{q.context or ''} {q.text}"
    if q.figures:
        return True, "has a figure or diagram"
    if _MATH.search(text) or _HARD_WORDS.search(text):
        return True, "maths, calculation or design"
    if q.marks is not None and q.marks >= 15:
        return True, f"{q.marks:g} marks"
    if prefs.technical_depth == "advanced" or prefs.detail == "detailed":
        return True, "advanced or detailed answer requested"
    return False, None


def answer_effort(prefs: AnswerPreferences, q: QuestionSpec) -> str | None:
    """Thinking effort per question: small questions do not need the deepest reasoning.

    Returns None to use the configured default (LLM_EFFORT)."""
    from app.config import get_settings

    s = get_settings()
    if not s.llm_adaptive_effort:
        return None
    small = (q.marks is not None and q.marks <= 3) or prefs.detail == "concise"
    hard = prefs.technical_depth == "advanced" or prefs.detail == "detailed" or bool(q.figures)
    if small and not hard:
        return "medium"
    return None


def generate_answer(
    provider: LLMProvider,
    *,
    subject: str | None,
    assignment_instructions: str | None,
    prefs: AnswerPreferences,
    q: QuestionSpec,
    references: list[str],
) -> AnswerOutcome:
    images = q.figures[:4] if provider.supports_images else []
    prompt = build_user_prompt(
        subject=subject,
        prefs=prefs,
        number=q.number,
        question=q.text,
        marks=q.marks,
        context=q.context,
        instructions=q.instructions,
        assignment_instructions=assignment_instructions,
        references=references,
        has_images=bool(images),
        max_words=q.max_words,
    )
    words = target_words(prefs, q.marks)
    if q.max_words:
        words = min(words, q.max_words)
    max_tokens = min(getattr(provider, "max_output_tokens", 64000), 16000 + words * 4)
    tin = tout = 0
    last: Exception | None = None
    need = min_words(words)
    # Budgets: one retry for unusable output, one rewrite when the answer is shorter than required.
    invalid = 0
    short_by: int | None = None
    best = None  # first valid but too-short answer: (content, parsed, result)
    final = None
    while True:
        p = prompt
        if short_by is not None:
            p += (
                f"\n\nYour previous answer had only {short_by} words. Rewrite it with at least {need} "
                f"and at most {words} words, developing the key points further. Reply with the JSON object only."
            )
        elif invalid:
            p += "\n\nYour previous reply could not be used. Reply with the JSON object only, with a non-empty answer."
        try:
            res = provider.complete_json(
                SYSTEM_PROMPT, p, ANSWER_SCHEMA, max_tokens=max_tokens, images=images, effort=answer_effort(prefs, q)
            )
            tin += res.input_tokens
            tout += res.output_tokens
            out = _AnswerOut.model_validate(res.data)
            content = out.answer.strip()
            if not content:
                raise LLMInvalidOutput("The model returned an empty answer")
        except (LLMInvalidOutput, ValidationError) as exc:
            last = (
                exc
                if isinstance(exc, LLMInvalidOutput)
                else LLMInvalidOutput("The model output did not match the expected format")
            )
            invalid += 1
            log.info("invalid model output", extra={"attempt": invalid, "rewrite": short_by is not None})
            if short_by is not None or invalid > MAX_INVALID_RETRIES:
                break  # a failed rewrite falls back to the shorter valid answer
            continue
        current = (content, out, res)
        if short_by is None and word_count(content) < need:
            best, short_by = current, word_count(content)
            log.info("answer shorter than required, asking for full length", extra={"words": short_by, "target": words})
            continue
        # Accept; after a rewrite keep whichever version is longer.
        final = current if best is None or word_count(content) >= word_count(best[0]) else best
        break
    chosen = final or best
    if chosen is None:
        assert last is not None
        raise last
    content, out, res = chosen
    wc = word_count(content)
    flags = [f.model_dump() for f in out.flags]
    if out.confidence == "low":
        flags.append({"type": "needs_verification", "message": "The model reported low confidence in this answer."})
    if wc < need:
        flags.append({"type": "other", "message": f"Answer is {wc} words, shorter than the {need} required."})
    elif wc < words * 0.4:  # safety net when the minimum-length rule is disabled
        flags.append({"type": "other", "message": f"Answer is {wc} words, shorter than the ~{words} requested."})
    if q.max_words and wc > q.max_words * 1.05:
        flags.append(
            {
                "type": "other",
                "message": f"Answer is {wc} words; about {q.max_words} fit in the space on the answer sheet.",
            }
        )
    elif wc > words * 1.8:
        flags.append({"type": "other", "message": f"Answer is {wc} words, longer than the ~{words} requested."})
    return AnswerOutcome(content, flags, res.model, tin, tout, cost(res.model, tin, tout), wc)
