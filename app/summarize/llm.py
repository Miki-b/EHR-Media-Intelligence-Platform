"""LLM clients behind one interface: summarize(context, feedback) -> (tool input dict, Usage).

- AnthropicClient: the `record_summary` tool with strict: true and tool_choice auto, plus a
  prompt that names the tool. Current models reject a forced tool_choice (Opus 5.5,
  Sonnet 5.5) or cannot combine it with thinking, which is on by default, and reject a
  non-default temperature (Haiku 5.5, Opus 5.5), so neither is sent; repeatability comes
  from the cache. No tool call, a refusal or a max_tokens stop is an InvalidOutput, which
  the caller retries once. The SDK retries network errors and 429/5xx (max_retries=4,
  exponential backoff, 60 s timeout); once those are exhausted it raises LLMUnavailable.
- OpenAIClient: optional (`pip install -e ".[openai]"`), JSON-schema response_format.
- MockClient: deterministic and offline, built only from the context text, so it goes
  through exactly the same parsing and checks as a real model.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Settings
from app.summarize.prompts import SYSTEM_PROMPT, user_message
from app.summarize.tool_schema import TOOL, TOOL_NAME, TOOL_SCHEMA

MAX_TOKENS = 4096  # thinking tokens count toward max_tokens on current models
TIMEOUT_SECONDS = 60.0
SDK_MAX_RETRIES = 4


class InvalidOutput(Exception):
    """The model answered, but not with a usable record_summary call."""


class LLMUnavailable(Exception):
    """The API could not be reached even after the SDK's retries."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    model: str

    def summarize(self, context: str, feedback: str | None = None) -> tuple[dict, Usage]: ...


class AnthropicClient:
    def __init__(self, settings: Settings) -> None:
        import anthropic

        self._anthropic = anthropic
        self.model = settings.llm_model
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key or None,  # None: SDK resolves env / profile
            max_retries=SDK_MAX_RETRIES,
            timeout=TIMEOUT_SECONDS,
        )

    def summarize(self, context: str, feedback: str | None = None) -> tuple[dict, Usage]:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=[TOOL],
                tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": user_message(context, feedback)}],
            )
        except (self._anthropic.APIConnectionError, self._anthropic.RateLimitError) as error:
            raise LLMUnavailable(type(error).__name__) from error
        except self._anthropic.APIStatusError as error:
            if error.status_code >= 500:
                raise LLMUnavailable(f"HTTP {error.status_code}") from error
            raise
        usage = Usage(response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason in ("refusal", "max_tokens"):
            raise InvalidOutput(f"stop_reason {response.stop_reason}")
        call = next(
            (b for b in response.content if b.type == "tool_use" and b.name == TOOL_NAME), None
        )
        if call is None:
            raise InvalidOutput(f"no {TOOL_NAME} tool call in the response")
        return dict(call.input), usage


class OpenAIClient:
    def __init__(self, settings: Settings) -> None:
        import openai

        self._openai = openai
        self.model = settings.llm_model
        self._client = openai.OpenAI(
            api_key=settings.openai_api_key or None,
            max_retries=SDK_MAX_RETRIES,
            timeout=TIMEOUT_SECONDS,
        )

    def summarize(self, context: str, feedback: str | None = None) -> tuple[dict, Usage]:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=MAX_TOKENS,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": TOOL_NAME, "schema": TOOL_SCHEMA, "strict": True},
                },
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message(context, feedback)},
                ],
            )
        except (self._openai.APIConnectionError, self._openai.RateLimitError) as error:
            raise LLMUnavailable(type(error).__name__) from error
        usage = Usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        try:
            return json.loads(response.choices[0].message.content or ""), usage
        except json.JSONDecodeError as error:
            raise InvalidOutput("response is not valid JSON") from error


# ---------------------------------------------------------------------------
# MockClient
# ---------------------------------------------------------------------------

LINE = re.compile(
    r"^\[(?P<id>[A-Za-z]+/[^\]]+)\] (?P<date>\S+)"
    r" \| (?P<cat>\w+) \| (?P<title>[^|]*) \| (?P<text>.*)$"
)
LAB_VALUE = re.compile(
    r"(?P<test>[^;:]+): (?P<value>-?[\d.]+) (?P<unit>[^(;]*?) ?\((?P<flag>High|Low)\b"
)
LEADING_CAPS = re.compile(r"^(?:[A-Z][A-Z0-9/&-]*\s+)+(?=[A-Z0-9][a-z0-9])")
NEGATION = re.compile(r"\b(no|not|without|negative for|denies|free of)\b[^.]*$", re.IGNORECASE)
DIAGNOSIS_TERMS = [  # (phrase shown, terms that state it)
    ("Diabetic ketoacidosis", ["diabetic ketoacidosis", "dka"]),
    ("Heart failure", ["heart failure", "chf"]),
    ("Community-acquired pneumonia", ["pneumonia"]),
    ("COPD", ["chronic obstructive pulmonary disease", "copd"]),
    ("Hip fracture", ["hip fracture"]),
    ("Type 2 diabetes mellitus", ["type 2 diabetes mellitus", "t2dm"]),
    ("Meniscal tear", ["meniscal tear", "meniscus"]),
    ("Tension-type headache", ["tension-type headache"]),
    ("Hypertension", ["hypertension", "htn"]),
    ("Cardiomegaly", ["cardiomegaly"]),
]
MAX_DIAGNOSES = 5
MAX_MEDIA = 5
NOTE_WORDS = 14


def _records(context: str) -> list[dict[str, str]]:
    return [m.groupdict() for line in context.splitlines() if (m := LINE.match(line))]


def _first_sentence(text: str, max_words: int = 25) -> str:
    sentence = LEADING_CAPS.sub("", text).split(". ")[0].strip().rstrip(".")
    return " ".join(sentence.split()[:max_words]) + "."


def _words(text: str, n: int) -> str:
    words = text.split()
    return " ".join(words[:n]) + ("..." if len(words) > n else "")


def _states(text: str, term: str) -> bool:
    """True when `term` appears in a sentence of `text` without a preceding negation."""
    lowered = text.lower()
    for match in re.finditer(rf"\b{re.escape(term)}\b", lowered):
        sentence_start = max(
            lowered.rfind(". ", 0, match.start()), lowered.rfind(": ", 0, match.start())
        )
        if not NEGATION.search(lowered[sentence_start + 1 : match.start()]):
            return True
    return False


def _diagnoses(records: list[dict[str, str]]) -> list[dict[str, Any]]:
    claims = []
    for phrase, terms in DIAGNOSIS_TERMS:
        cited = [
            r["id"] for r in records if any(_states(f"{r['title']}. {r['text']}", t) for t in terms)
        ][:3]
        if cited:
            claims.append({"text": phrase, "cited_ids": cited})
    return claims[:MAX_DIAGNOSES]


def _chief_concern(records: list[dict[str, str]]) -> str:
    docs = [r for r in records if r["cat"] == "DOC"]
    discharge = [r for r in docs if "discharge" in r["title"].lower()]
    source = (discharge or docs or records or [None])[0]
    return _first_sentence(source["text"]) if source else "Not documented."


def _media(records: list[dict[str, str]]) -> list[dict[str, str]]:
    reports = [r for r in records if r["cat"] in ("RAD", "LAB")][:MAX_MEDIA]
    return [
        {
            "resource_id": r["id"],
            "type": "imaging" if r["cat"] == "RAD" else "lab",
            "date": r["date"] if r["date"] != "undated" else "",
            "note": _words(_impression(r), NOTE_WORDS),
        }
        for r in reports
    ]


def _impression(record: dict[str, str]) -> str:
    text = record["text"]
    if "IMPRESSION:" in text:
        return text.split("IMPRESSION:", 1)[1].strip()
    return text


def _anomalies(records: list[dict[str, str]]) -> list[dict[str, Any]]:
    claims = []
    for r in records:
        if r["cat"] != "LAB":
            continue
        for m in LAB_VALUE.finditer(r["text"]):
            unit = m["unit"].strip()
            text = f"{m['test'].strip()} {m['value']} {unit} ({m['flag']}) on {r['date']}"
            claims.append({"text": " ".join(text.split()), "cited_ids": [r["id"]]})
    return claims


class MockClient:
    """Deterministic extractive summary; no network."""

    model = "mock"

    def summarize(self, context: str, feedback: str | None = None) -> tuple[dict, Usage]:
        records = _records(context)
        diagnoses, media, anomalies = _diagnoses(records), _media(records), _anomalies(records)
        cited = [i for c in diagnoses + anomalies for i in c["cited_ids"]]
        cited += [m["resource_id"] for m in media]
        draft = {
            "chief_concern": _chief_concern(records),
            "key_diagnoses": diagnoses,
            "recent_media": media,
            "anomalies": anomalies,
            "citations": list(dict.fromkeys(cited)),
        }
        return draft, Usage()


def get_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "anthropic":
        return AnthropicClient(settings)
    if settings.llm_provider == "openai":
        return OpenAIClient(settings)
    return MockClient()
