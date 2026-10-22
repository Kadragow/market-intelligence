"""Code-side validation of the model's report draft.

Checks, beyond the JSON schema:
- every claim cites at least one source id that exists in the input;
- research candidates refer to instruments with fresh, sufficient data;
- every number and date in the text appears in the input data.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from market_intelligence.ai.schema import Claim, ReportDraft, ReportInput

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_PL_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class ValidationOutcome:
    draft: ReportDraft | None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.draft is not None and not self.errors


def validate_draft(raw_json: str, report_input: ReportInput) -> ValidationOutcome:
    try:
        draft = ReportDraft.model_validate_json(raw_json)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:10]
        )
        return ValidationOutcome(None, [f"schema: {details}"])

    errors: list[str] = []
    known_sources = report_input.source_ids()
    news_text = {n.source_id: _strings(n) for n in report_input.news}
    trusted = NumberContext.from_strings(_strings(report_input.model_copy(update={"news": []})))

    for path, claim in _claims(draft):
        unknown = sorted(set(claim.source_ids) - known_sources)
        if unknown:
            errors.append(f"{path}: unknown source ids {unknown}")
        cited_news = [s for sid in claim.source_ids for s in news_text.get(sid, [])]
        errors.extend(f"{path}: {e}" for e in trusted.extended(cited_news).check(claim.text))

    usable = {i.instrument_id for i in report_input.instruments if i.data_status == "ok"}
    known_instruments = {i.instrument_id for i in report_input.instruments}
    for idx, cand in enumerate(draft.research_candidates):
        path = f"research_candidates[{idx}]"
        if cand.instrument_id not in known_instruments:
            errors.append(f"{path}: unknown instrument {cand.instrument_id!r}")
        elif cand.instrument_id not in usable:
            errors.append(f"{path}: instrument {cand.instrument_id} has stale or missing data")
        # Plain-text fields may only use numbers from code-computed data and from
        # news cited by this candidate's claims.
        cited = {sid for c in [cand.thesis, *cand.counterarguments] for sid in c.source_ids}
        context = trusted.extended([s for sid in cited for s in news_text.get(sid, [])])
        for name in ("risks", "uncertainties", "invalidation_conditions"):
            for j, text in enumerate(getattr(cand, name)):
                errors.extend(f"{path}.{name}[{j}]: {e}" for e in context.check(text))

    for j, text in enumerate(draft.limitations):
        errors.extend(f"limitations[{j}]: {e}" for e in trusted.check(text))

    return ValidationOutcome(draft, errors)


@dataclass(frozen=True)
class NumberContext:
    numbers: frozenset[float]
    dates: frozenset[tuple[int, int, int]]

    @classmethod
    def from_strings(cls, strings: Iterable[str]) -> NumberContext:
        numbers: set[float] = set()
        dates: set[tuple[int, int, int]] = set()
        for text in strings:
            found_dates, rest = _extract_dates(text)
            dates.update(found_dates)
            numbers.update(abs(_to_float(m)) for m in _NUMBER_RE.findall(rest))
        return cls(frozenset(numbers), frozenset(dates))

    def extended(self, strings: Iterable[str]) -> NumberContext:
        other = NumberContext.from_strings(strings)
        return NumberContext(self.numbers | other.numbers, self.dates | other.dates)

    def check(self, text: str) -> list[str]:
        errors: list[str] = []
        found_dates, rest = _extract_dates(text)
        for d in sorted(found_dates - self.dates):
            errors.append(f"date {d[0]:04d}-{d[1]:02d}-{d[2]:02d} not in input")
        for token in _NUMBER_RE.findall(rest):
            if not self._matches(token):
                errors.append(f"number {token!r} not in input")
        return errors

    def _matches(self, token: str) -> bool:
        value = abs(_to_float(token))
        decimals = len(token.replace(",", ".").partition(".")[2])
        # DECISION: a number written with d decimals matches an input value within
        # half a unit of the d-th decimal (i.e. a correct rounding). Signs are ignored
        # because the text may say "spadek o 0,45%" for -0.45.
        tolerance = 0.5 * 10**-decimals + 1e-9
        return any(abs(value - known) <= tolerance for known in self.numbers)


def _to_float(token: str) -> float:
    return float(token.replace(",", "."))


def _extract_dates(text: str) -> tuple[set[tuple[int, int, int]], str]:
    dates: set[tuple[int, int, int]] = set()
    for y, m, d in _ISO_DATE_RE.findall(text):
        dates.add((int(y), int(m), int(d)))
    for d, m, y in _PL_DATE_RE.findall(text):
        dates.add((int(y), int(m), int(d)))
    rest = _PL_DATE_RE.sub(" ", _ISO_DATE_RE.sub(" ", text))
    return dates, rest


def _strings(model: BaseModel) -> list[str]:
    """All scalar values of a model, rendered as strings."""
    out: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif value is not None:
            out.append(value if isinstance(value, str) else json.dumps(value))

    walk(model.model_dump(mode="json"))
    return out


def _claims(draft: ReportDraft) -> Iterator[tuple[str, Claim]]:
    for name in (
        "summary",
        "global_markets",
        "macro",
        "sectors_and_companies",
        "warning_signs",
        "technology",
    ):
        for idx, claim in enumerate(getattr(draft, name)):
            yield f"{name}[{idx}]", claim
    for idx, cand in enumerate(draft.research_candidates):
        yield f"research_candidates[{idx}].thesis", cand.thesis
        for j, claim in enumerate(cand.counterarguments):
            yield f"research_candidates[{idx}].counterarguments[{j}]", claim
    for name in ("positive", "base", "negative"):
        yield f"scenarios.{name}", getattr(draft.scenarios, name)
