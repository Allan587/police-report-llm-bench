"""Read-only validation of the original inputs required for a main review."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Sequence

from automation.contracts import BenchmarkModel, JSONValue


class InputIssueCategory(str, Enum):
    MISSING = "missing"
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class InputIssue:
    category: InputIssueCategory
    code: str
    message: str
    path: Optional[Path] = None
    model: Optional[BenchmarkModel] = None


@dataclass(frozen=True)
class ReviewInputValidation:
    statement_index: int
    selected_run_id: Optional[str]
    statement: Optional[Mapping[str, JSONValue]]
    outputs: Mapping[BenchmarkModel, Mapping[str, JSONValue]] = field(default_factory=dict)
    issues: Sequence[InputIssue] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "outputs", MappingProxyType(dict(self.outputs)))
        object.__setattr__(self, "issues", tuple(self.issues))

    def issues_by_category(self, category: InputIssueCategory) -> tuple[InputIssue, ...]:
        return tuple(issue for issue in self.issues if issue.category is category)

    @property
    def missing(self) -> tuple[InputIssue, ...]:
        return self.issues_by_category(InputIssueCategory.MISSING)

    @property
    def invalid(self) -> tuple[InputIssue, ...]:
        return self.issues_by_category(InputIssueCategory.INVALID)

    @property
    def duplicates(self) -> tuple[InputIssue, ...]:
        return self.issues_by_category(InputIssueCategory.DUPLICATE)

    @property
    def ambiguities(self) -> tuple[InputIssue, ...]:
        return self.issues_by_category(InputIssueCategory.AMBIGUOUS)

    @property
    def dependencies(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    @property
    def inputs_complete(self) -> bool:
        return (
            not self.issues
            and self.statement is not None
            and self.selected_run_id is not None
            and set(self.outputs) == set(BenchmarkModel)
        )


_MODEL_LAYOUT: Mapping[BenchmarkModel, tuple[str, str]] = MappingProxyType(
    {
        BenchmarkModel.GEMMA: ("gemma_2b", "gemma_2b"),
        BenchmarkModel.MISTRAL: ("mistral", "mistral"),
        BenchmarkModel.LLAMA: ("llama_3_2", "llama_3_2"),
    }
)


def _issue(
    issues: list[InputIssue],
    category: InputIssueCategory,
    code: str,
    message: str,
    *,
    path: Optional[Path] = None,
    model: Optional[BenchmarkModel] = None,
) -> None:
    issues.append(InputIssue(category, code, message, path, model))


def _read_jsonl(path: Path, issues: list[InputIssue], code_prefix: str) -> list[dict]:
    if not path.is_file():
        _issue(
            issues,
            InputIssueCategory.MISSING,
            f"{code_prefix}_file_missing",
            f"required JSONL file is missing: {path}",
            path=path,
        )
        return []

    records: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    continue
                try:
                    value = json.loads(raw_line)
                except json.JSONDecodeError as error:
                    _issue(
                        issues,
                        InputIssueCategory.INVALID,
                        f"{code_prefix}_json_invalid",
                        f"invalid JSON at line {line_number}: {error.msg}",
                        path=path,
                    )
                    continue
                if not isinstance(value, dict):
                    _issue(
                        issues,
                        InputIssueCategory.INVALID,
                        f"{code_prefix}_record_invalid",
                        f"line {line_number} must contain a JSON object",
                        path=path,
                    )
                    continue
                records.append(value)
    except (OSError, UnicodeError) as error:
        _issue(
            issues,
            InputIssueCategory.INVALID,
            f"{code_prefix}_unreadable",
            f"could not read {path}: {error}",
            path=path,
        )
    return records


def _valid_index(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _load_statement(
    path: Path, statement_index: int, issues: list[InputIssue]
) -> Optional[Mapping[str, JSONValue]]:
    records = _read_jsonl(path, issues, "statement")
    matches: list[dict] = []
    for record in records:
        index = record.get("statement_index")
        if not _valid_index(index):
            _issue(
                issues,
                InputIssueCategory.INVALID,
                "statement_index_invalid",
                "statement_index must be a non-negative integer",
                path=path,
            )
            continue
        if index == statement_index:
            matches.append(record)

    if not matches:
        _issue(
            issues,
            InputIssueCategory.MISSING,
            "statement_missing",
            f"statement_index={statement_index} is not present",
            path=path,
        )
        return None
    if len(matches) > 1:
        _issue(
            issues,
            InputIssueCategory.DUPLICATE,
            "statement_duplicate",
            f"statement_index={statement_index} appears {len(matches)} times",
            path=path,
        )
        return None

    statement = matches[0]
    content = statement.get("statement")
    report = content.get("report") if isinstance(content, Mapping) else None
    if not isinstance(report, str) or not report.strip():
        _issue(
            issues,
            InputIssueCategory.INVALID,
            "statement_content_invalid",
            "statement.report must be a non-empty string",
            path=path,
        )
        return None
    return MappingProxyType(dict(statement))


def validate_output_record(
    record: Mapping[str, JSONValue],
    *,
    expected_statement_index: int,
    expected_model: BenchmarkModel,
    expected_run_id: str,
    path: Optional[Path] = None,
) -> tuple[InputIssue, ...]:
    """Validate one raw output without assigning or correcting its identity."""

    issues: list[InputIssue] = []
    record_index = record.get("statement_index")
    if not _valid_index(record_index) or record_index != expected_statement_index:
        _issue(
            issues,
            InputIssueCategory.INVALID,
            "output_statement_mismatch",
            "output statement_index does not match the requested statement",
            path=path,
            model=expected_model,
        )
    expected_raw_model = _MODEL_LAYOUT[expected_model][1]
    if record.get("model_name") != expected_raw_model:
        _issue(
            issues,
            InputIssueCategory.INVALID,
            "output_model_mismatch",
            f"model_name must be {expected_raw_model!r}",
            path=path,
            model=expected_model,
        )
    if record.get("run_id") != expected_run_id:
        _issue(
            issues,
            InputIssueCategory.INVALID,
            "output_run_mismatch",
            f"output run_id must match selected run {expected_run_id!r}",
            path=path,
            model=expected_model,
        )
    report = record.get("draft_report")
    if not isinstance(report, str) or not report.strip():
        _issue(
            issues,
            InputIssueCategory.INVALID,
            "output_report_invalid",
            "draft_report must be a non-empty string",
            path=path,
            model=expected_model,
        )
    return tuple(issues)


def _model_output_path(run_path: Path, model: BenchmarkModel) -> Path:
    run_directory, _ = _MODEL_LAYOUT[model]
    return run_path / run_directory / "generation" / "draft_reports.jsonl"


def _run_contains_statement(run_path: Path, statement_index: int) -> bool:
    for model in BenchmarkModel:
        path = _model_output_path(run_path, model)
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    if not raw_line.strip():
                        continue
                    value = json.loads(raw_line)
                    if (
                        isinstance(value, dict)
                        and _valid_index(value.get("statement_index"))
                        and value.get("statement_index") == statement_index
                    ):
                        return True
        except (json.JSONDecodeError, OSError, UnicodeError):
            continue
    return False


def _select_run(
    runs_root: Path,
    statement_index: int,
    requested_run: Optional[str | Path],
    issues: list[InputIssue],
) -> Optional[Path]:
    root = runs_root.resolve()
    if requested_run is not None:
        requested_path = Path(requested_run)
        candidate = requested_path if requested_path.is_absolute() else runs_root / requested_path
        resolved = candidate.resolve()
        if resolved.parent != root:
            _issue(
                issues,
                InputIssueCategory.INVALID,
                "run_outside_root",
                "selected run must be an immediate child of runs_root",
                path=candidate,
            )
            return None
        if not resolved.is_dir():
            _issue(
                issues,
                InputIssueCategory.MISSING,
                "run_missing",
                f"selected run does not exist: {candidate}",
                path=candidate,
            )
            return None
        return resolved

    if not root.is_dir():
        _issue(
            issues,
            InputIssueCategory.MISSING,
            "runs_root_missing",
            f"runs root does not exist: {runs_root}",
            path=runs_root,
        )
        return None
    candidates = sorted(
        path.resolve()
        for path in root.iterdir()
        if path.is_dir() and _run_contains_statement(path, statement_index)
    )
    if not candidates:
        _issue(
            issues,
            InputIssueCategory.MISSING,
            "run_candidate_missing",
            f"no run contains statement_index={statement_index}",
            path=runs_root,
        )
        return None
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        _issue(
            issues,
            InputIssueCategory.AMBIGUOUS,
            "run_candidates_ambiguous",
            f"multiple runs contain statement_index={statement_index}: {names}",
            path=runs_root,
        )
        return None
    return candidates[0]


def _validate_metadata(run_path: Path, issues: list[InputIssue]) -> None:
    path = run_path / "metadata.json"
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as handle:
            metadata = json.load(handle)
    except (json.JSONDecodeError, OSError, UnicodeError) as error:
        _issue(
            issues,
            InputIssueCategory.INVALID,
            "run_metadata_invalid",
            f"invalid run metadata: {error}",
            path=path,
        )
        return
    if not isinstance(metadata, dict) or metadata.get("run_id") != run_path.name:
        _issue(
            issues,
            InputIssueCategory.INVALID,
            "run_metadata_mismatch",
            "metadata run_id must match the selected run directory",
            path=path,
        )


def _load_outputs(
    run_path: Path,
    statement_index: int,
    issues: list[InputIssue],
) -> Mapping[BenchmarkModel, Mapping[str, JSONValue]]:
    outputs: dict[BenchmarkModel, Mapping[str, JSONValue]] = {}
    for model in BenchmarkModel:
        path = _model_output_path(run_path, model)
        model_issues: list[InputIssue] = []
        records = _read_jsonl(path, model_issues, f"{model.value}_output")
        issues.extend(
            InputIssue(issue.category, issue.code, issue.message, issue.path, model)
            for issue in model_issues
        )
        matches = [
            record
            for record in records
            if _valid_index(record.get("statement_index"))
            and record.get("statement_index") == statement_index
        ]
        if not matches:
            if len(records) == 1 and _valid_index(records[0].get("statement_index")):
                issues.extend(
                    validate_output_record(
                        records[0],
                        expected_statement_index=statement_index,
                        expected_model=model,
                        expected_run_id=run_path.name,
                        path=path,
                    )
                )
            else:
                _issue(
                    issues,
                    InputIssueCategory.MISSING,
                    "output_missing",
                    f"no {model.value} output for statement_index={statement_index}",
                    path=path,
                    model=model,
                )
            continue
        if len(matches) > 1:
            _issue(
                issues,
                InputIssueCategory.DUPLICATE,
                "output_duplicate",
                f"{model.value} has {len(matches)} outputs for statement_index={statement_index}",
                path=path,
                model=model,
            )
            continue
        record = matches[0]
        record_issues = validate_output_record(
            record,
            expected_statement_index=statement_index,
            expected_model=model,
            expected_run_id=run_path.name,
            path=path,
        )
        issues.extend(record_issues)
        if not record_issues:
            outputs[model] = MappingProxyType(dict(record))
    return outputs


def inspect_review_inputs(
    *,
    statement_index: int,
    statements_path: Path,
    runs_root: Path,
    run: Optional[str | Path] = None,
) -> ReviewInputValidation:
    """Inspect one statement and one run without creating or changing files."""

    if not _valid_index(statement_index):
        raise ValueError("statement_index must be a non-negative integer")

    issues: list[InputIssue] = []
    statement = _load_statement(Path(statements_path), statement_index, issues)
    run_path = _select_run(Path(runs_root), statement_index, run, issues)
    outputs: Mapping[BenchmarkModel, Mapping[str, JSONValue]] = {}
    selected_run_id: Optional[str] = None
    if run_path is not None:
        selected_run_id = run_path.name
        _validate_metadata(run_path, issues)
        outputs = _load_outputs(run_path, statement_index, issues)

    return ReviewInputValidation(
        statement_index=statement_index,
        selected_run_id=selected_run_id,
        statement=statement,
        outputs=outputs,
        issues=issues,
    )
