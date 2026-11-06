"""Validate an operator's existing JSON/JSONL inputs and stage an isolated copy."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from automation.orchestrator import OrchestratorConfig
from automation.review.input_loader import inspect_review_inputs
from .config import PortableConfig, PortableConfigError, _no_symlink, _under


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sources(config: PortableConfig) -> dict[str, str]:
    root = config.input_root
    if not root.is_dir():
        raise PortableConfigError("inputs.root is missing or is not a directory")
    if (root / "portable_manifest.json").exists():
        raise PortableConfigError("input root contains a portable output manifest")
    cases_root = root / "review_cases"
    if cases_root.exists():
        for child in cases_root.iterdir():
            if child.is_symlink() or not child.is_dir() or child.name not in {f"statement_{i:03d}" for i in range(10)} or any(child.iterdir()):
                raise PortableConfigError("input review_cases contains existing case data")
    files: dict[str, str] = {}
    def unreadable(error: OSError) -> None:
        raise PortableConfigError("an input directory cannot be read") from error

    for current, dirs, names in os.walk(root, followlinks=False, onerror=unreadable):
        for name in (*dirs, *names):
            child = Path(current) / name
            _no_symlink(child, "input")
            _under(child, config.allowed_input_roots, "input")
        for name in names:
            path = Path(current) / name
            if not path.is_file():
                raise PortableConfigError("input contains a non-regular file")
            try:
                files[path.relative_to(root).as_posix()] = _digest(path)
            except OSError as error:
                raise PortableConfigError("an input file cannot be read") from error
    return files


def _indices(config: PortableConfig) -> tuple[tuple[int, str], ...]:
    try:
        raw_lines = config.statements_path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in raw_lines if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PortableConfigError("statements.jsonl is unreadable or invalid") from error
    indices: list[int] = []
    for record in records:
        if not isinstance(record, dict) or type(record.get("statement_index")) is not int or record["statement_index"] < 0:
            raise PortableConfigError("statement_index is invalid")
        indices.append(record["statement_index"])
    if len(indices) != len(set(indices)):
        raise PortableConfigError("duplicate statement_index")
    active = sorted(index for index in indices if index >= 10)
    if not active:
        raise PortableConfigError("no reviewable statements (index >= 10) in input")
    if active != list(range(10, active[-1] + 1)):
        raise PortableConfigError("reviewable statement indices must be contiguous from 10")
    result = []
    for index in active:
        inspected = inspect_review_inputs(statement_index=index,
                                          statements_path=config.statements_path,
                                          runs_root=config.runs_root)
        if not inspected.inputs_complete:
            codes = ", ".join(issue.code for issue in inspected.issues)
            raise PortableConfigError(f"statement {index} has invalid review inputs: {codes}")
        result.append((index, inspected.selected_run_id))
    return tuple(result)


def _validate_supporting_files(config: PortableConfig) -> None:
    for model in ("gemma", "mistral", "llama"):
        for folder, suffix in (("feedback", "history.jsonl"), ("profiles", "profile.json")):
            path = config.input_root / folder / f"{model}_{suffix}"
            try:
                raw = path.read_text(encoding="utf-8")
                values = [json.loads(line) for line in raw.splitlines() if line.strip()] if suffix.endswith("jsonl") else [json.loads(raw)]
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise PortableConfigError(f"{folder} input for {model} is missing or invalid") from error
            if not values or any(not isinstance(value, dict) for value in values):
                raise PortableConfigError(f"{folder} input for {model} has invalid records")


@dataclass(frozen=True)
class PreparedInputs:
    workflow: OrchestratorConfig
    cases: tuple[tuple[int, str], ...]
    source_sha256: str


class InputAdapter:
    """Copy validated source files once; the existing workflow owns later writes."""

    def __init__(self, config: PortableConfig):
        self.config = config

    def inspect(self) -> PreparedInputs:
        files = _sources(self.config)
        cases = _indices(self.config)
        _validate_supporting_files(self.config)
        source_hash = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        root = self.config.output_root
        workflow = OrchestratorConfig(root / "statements.jsonl", root / "runs",
                                      root / "review_cases", root / "feedback", root / "profiles")
        return PreparedInputs(workflow, cases, source_hash)

    def prepare(self) -> PreparedInputs:
        inspected = self.inspect()
        root = self.config.output_root
        _no_symlink(root, "output")
        if root.exists():
            manifest = root / "portable_manifest.json"
            try:
                current = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise PortableConfigError("output already exists without a valid manifest") from error
            if current != self._manifest(inspected):
                raise PortableConfigError("output belongs to different or changed inputs")
            return inspected
        root.parent.mkdir(parents=True, exist_ok=True)
        _no_symlink(root.parent, "output")
        staged = Path(tempfile.mkdtemp(prefix=".portable-", dir=root.parent))
        try:
            for source in self.config.input_root.rglob("*"):
                relative = source.relative_to(self.config.input_root)
                target = staged / relative
                if source.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copyfile(source, target)
                    except OSError as error:
                        raise PortableConfigError("an input file changed or cannot be copied") from error
            copied = {path.relative_to(staged).as_posix(): _digest(path)
                      for path in staged.rglob("*") if path.is_file()}
            if copied != _sources(self.config):
                raise PortableConfigError("input changed during preparation")
            cases_root = staged / "review_cases"
            cases_root.mkdir(exist_ok=True)
            # O-01 advances sequentially. Precreating later directories would
            # make the selector mistake the last one for the active case.
            first_index = inspected.cases[0][0]
            (cases_root / f"statement_{first_index:03d}").mkdir(exist_ok=True)
            for dirname in ("feedback", "profiles"):
                (staged / dirname).mkdir(exist_ok=True)
            (staged / "portable_manifest.json").write_text(
                json.dumps(self._manifest(inspected), sort_keys=True, indent=2) + "\n", encoding="utf-8")
            if root.exists():
                raise PortableConfigError("output appeared during preparation")
            staged.rename(root)
        finally:
            if staged.exists():
                shutil.rmtree(staged)
        return inspected

    @staticmethod
    def _manifest(inspected: PreparedInputs) -> dict:
        return {"schema_version": 1, "source_sha256": inspected.source_sha256,
                "cases": [{"statement_index": index, "run_id": run} for index, run in inspected.cases]}
