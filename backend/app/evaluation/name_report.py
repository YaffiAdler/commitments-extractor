"""The Markdown report of a name evaluation run.

Holds counts, rates and single letters only — never a name, a file name or
a model note — so it can be shared. Synthetic and handwritten pages are
reported apart, and synthetic results are marked as a sanity check.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from app.evaluation.name_metrics import (
    AGREE,
    DISAGREE,
    NAME_FIELDS,
    NOT_COMPARED,
    Confusions,
    FieldOutcome,
    ImageOutcome,
    Summary,
    confusions,
    summarize,
)
from app.evaluation.name_set import SampleKind
from app.hebrew_text import FINAL_LETTERS, REGULAR_LETTERS

# Letters often confused in Hebrew handwriting; each group's mix-ups are counted together.
WATCHED_GROUPS = ("דר", "וןזג", "החת", "כב", "סםט", "עצ")
FINAL_PAIRS = tuple(final + regular for final, regular in zip(FINAL_LETTERS, REGULAR_LETTERS))
TOP_SUBSTITUTIONS = 15
TOP_EDITS = 10
SPACE_SYMBOL = "␣"
GROUP_SEPARATOR = "/"
BOTH_FIELDS = "both fields"
NOT_AVAILABLE = "—"

SYNTHETIC_NOTE = (
    "> **Synthetic pages are a sanity check only.** They are rendered from a font, so they show whether the "
    "tooling works and catch gross failures; they say nothing reliable about accuracy on real handwriting."
)
NORMALIZATION_NOTE = (
    "Exact and CER compare after trimming, collapsing whitespace, removing quotes and niqqud and unifying final "
    "letters (ך/כ ם/מ ן/נ ף/פ ץ/צ). \"Exact, finals as written\" and the letter tables keep final letters apart. "
    "A field left empty counts as all-deleted in CER and is not in the letter tables."
)


def render(outcomes: list[ImageOutcome], configuration: Mapping[str, str]) -> str:
    """The report of one run; ``configuration`` names the settings it ran with (no secrets)."""
    kinds = [kind for kind in SampleKind if any(o.sample.kind is kind for o in outcomes)]
    counts = Counter(o.sample.kind for o in outcomes)
    lines = [
        "# Name reading evaluation",
        "",
        "Configuration: " + ", ".join(f"{key} `{value}`" for key, value in configuration.items()) + ".",
        f"Pages: {len(outcomes)} (" + ", ".join(f"{kind.value} {counts[kind]}" for kind in kinds) + ").",
        "",
    ]
    if SampleKind.SYNTHETIC in kinds:
        lines += [SYNTHETIC_NOTE, ""]
    lines += [NORMALIZATION_NOTE, ""]
    lines += _accuracy_section(outcomes, kinds)
    if any(f.agreement != NOT_COMPARED or f.candidates for o in outcomes for f in o.fields):
        lines += _second_look_section(outcomes, kinds)
    for kind in kinds:
        lines += confusion_section(f"Letter confusions — {kind.value}", confusions(_fields_of(outcomes, kind)))
    lines += _pipeline_section(outcomes, kinds)
    return "\n".join(lines) + "\n"


def _fields_of(outcomes: Iterable[ImageOutcome], kind: SampleKind, name: str | None = None) -> list[FieldOutcome]:
    return [f for o in outcomes if o.sample.kind is kind for f in o.fields if name is None or f.name == name]


def rate(part: int, whole: int) -> str:
    return f"{part} ({part / whole:.0%})" if whole else "0"


def _accuracy_row(kind: SampleKind, label: str, s: Summary) -> str:
    cer = f"{s.cer:.1%}" if s.cer is not None else NOT_AVAILABLE
    return (f"| {kind.value} | {label} | {s.fields} | {rate(s.exact, s.fields)} | {rate(s.exact_strict, s.fields)} "
            f"| {s.left_empty} | {cer} | {s.flagged} | {s.wrong_not_flagged} |")


def _accuracy_section(outcomes: list[ImageOutcome], kinds: list[SampleKind]) -> list[str]:
    lines = [
        "## Accuracy",
        "",
        "| Kind | Field | Fields | Exact | Exact, finals as written | Left empty | CER | Flagged for review "
        "| Wrong and not flagged |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for kind in kinds:
        for name in NAME_FIELDS:
            lines.append(_accuracy_row(kind, name, summarize(_fields_of(outcomes, kind, name))))
        lines.append(_accuracy_row(kind, BOTH_FIELDS, summarize(_fields_of(outcomes, kind))))
    lines += ["", "| Kind | Pages | Both names exact |", "|---|---|---|"]
    for kind in kinds:
        pages = [o for o in outcomes if o.sample.kind is kind]
        lines.append(f"| {kind.value} | {len(pages)} | {rate(sum(o.both_exact for o in pages), len(pages))} |")
    return lines + [""]


def _second_look_section(outcomes: list[ImageOutcome], kinds: list[SampleKind]) -> list[str]:
    """Whether the name second look's agreement goes with a right name, and candidate hits."""
    lines = ["## Name second look", "",
             "| Kind | Readings | Fields | Exact | Wrong | Wrong, right name among the candidates |",
             "|---|---|---|---|---|---|"]
    for kind in kinds:
        for agreement in (AGREE, DISAGREE, NOT_COMPARED):
            fields = [f for f in _fields_of(outcomes, kind) if f.agreement == agreement]
            wrong = [f for f in fields if not f.exact]
            lines.append(f"| {kind.value} | {agreement} | {len(fields)} | {sum(f.exact for f in fields)} "
                         f"| {len(wrong)} | {sum(f.truth_in_candidates for f in wrong)} |")
    return lines + [""]


def letter(char: str) -> str:
    return SPACE_SYMBOL if char.isspace() else char


def confusion_section(title: str, table: Confusions) -> list[str]:
    """Watched groups, top substitutions, dropped and added letters (single letters only)."""
    lines = [f"## {title}", "", "Watched groups (written → read, both directions):", "",
             "| Group | Written | Read | Count |", "|---|---|---|---|"]
    for group in (*WATCHED_GROUPS, *FINAL_PAIRS):
        label = GROUP_SEPARATOR.join(group)
        pairs = [(pair, n) for pair, n in table.substitutions.most_common() if set(pair) <= set(group)]
        lines += [f"| {label} | {written} | {read} | {n} |" for (written, read), n in pairs] or [f"| {label} | | | 0 |"]
    lines += ["", f"Most frequent substitutions (top {TOP_SUBSTITUTIONS}):", "",
              "| Written | Read | Count |", "|---|---|---|"]
    lines += [f"| {letter(w)} | {letter(r)} | {n} |" for (w, r), n in table.substitutions.most_common(TOP_SUBSTITUTIONS)]
    lines += _edit_rows("Letters dropped (written, not read)", table.deletions)
    lines += _edit_rows("Letters added (read, not written)", table.insertions)
    return lines + [""]


def _edit_rows(title: str, counts: Counter[str]) -> list[str]:
    lines = ["", f"{title}, top {TOP_EDITS}:", "", "| Letter | Count |", "|---|---|"]
    return lines + ([f"| {letter(c)} | {n} |" for c, n in counts.most_common(TOP_EDITS)] or ["| — | 0 |"])


def _pipeline_section(outcomes: list[ImageOutcome], kinds: list[SampleKind]) -> list[str]:
    errors = Counter(o.error for o in outcomes if o.error)
    lines = [
        "## Pipeline outcomes",
        "",
        f"Pages with no form read: {sum(o.forms == 0 or o.error is not None for o in outcomes)}. "
        f"Pages split into several forms: {sum(o.forms > 1 for o in outcomes)}.",
        "",
    ]
    lines += [f"- {error}: {count}" for error, count in errors.most_common()]
    lines += ["", "| Kind | Name fields by source | Reason codes on name fields |", "|---|---|---|"]
    for kind in kinds:
        fields = _fields_of(outcomes, kind)
        sources = Counter(f.source.value for f in fields)
        reasons = Counter(r for f in fields for r in f.reasons)
        lines.append(f"| {kind.value} | {_counts(sources)} | {_counts(reasons)} |")
    return lines


def _counts(counts: Counter[str]) -> str:
    return ", ".join(f"{key} {n}" for key, n in sorted(counts.items())) or NOT_AVAILABLE
