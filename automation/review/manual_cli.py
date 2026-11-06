"""Small local CLI for synthetic operator-supplied review packages."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from automation.contracts import Reviewer
from automation.orchestrator import OrchestratorConfig
from automation.workflow_coordinator import WorkflowCoordinatorConfig

from .manual_import_adapter import ManualReviewImportError
from .manual_supervisor import (
    ManualSupervisorError, _atomic_json, import_manual_package, manual_status,
    prepare_manual_request, template_for,
)
from .vertical_flow import VerticalReviewConfig, VerticalReviewStatus


def _config(root: Path) -> VerticalReviewConfig:
    root = root.absolute()
    temporary = Path(tempfile.gettempdir()).resolve()
    if (root.is_symlink() or not root.resolve().is_relative_to(temporary)
            or root.resolve() == temporary or not root.is_dir()):
        raise ManualSupervisorError("synthetic_root_required")
    operational = root / "operational"
    workflow = OrchestratorConfig(
        operational / "statements.jsonl", operational / "runs",
        operational / "review_cases", operational / "feedback",
        operational / "profiles",
    )
    coordinator = WorkflowCoordinatorConfig(
        workflow, root / "runtime" / "workflow.lock",
        "manual-supervised-cli", lease_location_confirmed=True,
    )
    return VerticalReviewConfig(coordinator, root, fixture_location_confirmed=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True,
                        help="dedicated synthetic fixture directory under system temporary root")
    parser.add_argument("--inbox", type=Path, required=True,
                        help="private package directory inside the fixture root")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pending")
    sub.add_parser("prepare")
    status = sub.add_parser("status")
    status.add_argument("--package", type=Path)
    importing = sub.add_parser("import")
    importing.add_argument("package", type=Path)
    importing.add_argument("--reviewer", required=True, choices=[item.value for item in Reviewer])
    args = parser.parse_args(argv)
    try:
        config = _config(args.root)
        inbox = args.inbox.absolute()
        if (inbox.is_symlink() or not inbox.resolve().is_relative_to(config.fixture_root)
                or not inbox.is_dir()):
            raise ManualSupervisorError("unsafe_inbox")
        if args.command == "prepare":
            request = prepare_manual_request(config)
            template = inbox / f"manual-template-{request.reviewer.value}-{request.attempt_id}.json"
            if template.is_symlink():
                raise ManualSupervisorError("unsafe_template")
            if not template.exists():
                _atomic_json(template, template_for(request))
            result = {"request": request.as_dict(), "template": str(template)}
        elif args.command == "import":
            outcome = import_manual_package(
                config, args.package, allowed_root=inbox, reviewer=Reviewer(args.reviewer))
            result = {"status": outcome.status.value, "reason": outcome.reason,
                      "reviewers": manual_status(config)}
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if outcome.status in {
                VerticalReviewStatus.COMPLETED, VerticalReviewStatus.UNCHANGED,
            } else 2
        else:
            result = manual_status(
                config, package_path=getattr(args, "package", None),
                allowed_root=inbox if getattr(args, "package", None) else None,
            )
            if args.command == "pending":
                result = {"statement_index": result["statement_index"],
                          "plan_action": result["plan_action"],
                          "valid_reviewers": result["valid_reviewers"],
                          "missing_reviewers": result["missing_reviewers"],
                          "adjudication_enabled": result["adjudication_enabled"],
                          "pending": {name: item for name, item in result["reviewers"].items()
                                      if item["state"] != "PUBLISHED"}}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ManualSupervisorError, ManualReviewImportError, ValueError, OSError) as error:
        code = error.code if hasattr(error, "code") else type(error).__name__
        print(json.dumps({"error": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
