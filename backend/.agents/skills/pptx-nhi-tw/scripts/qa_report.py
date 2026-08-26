"""Create and validate QAReport v1 bound to a final PPTX byte stream."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from check_pptx_content import check_pptx_content
from contracts import CONTENT_CHECK_VERSION, ContractError, EVIDENCE_MAP_VERSION, QA_REPORT_VERSION, load_json, require_object, sha256_file, write_json


VALID_CHECK_STATUSES = {"pass", "fail", "not_run"}


def _content_report(content_check_path: Path, pptx_path: Path) -> dict[str, Any]:
    report = require_object(load_json(content_check_path), "content check")
    if report.get("format_version") != CONTENT_CHECK_VERSION:
        raise ContractError(f"content check.format_version must be {CONTENT_CHECK_VERSION!r}")
    presentation = require_object(report.get("presentation"), "content check.presentation")
    current = check_pptx_content(pptx_path)["presentation"]
    if presentation.get("sha256") != current["sha256"]:
        raise ContractError("content check SHA-256 does not match the final PPTX")
    if presentation.get("slide_count") != current["slide_count"]:
        raise ContractError("content check slide_count does not match the final PPTX")
    if report.get("status") != "pass":
        raise ContractError("content check must pass before creating a QA report")
    return report


def create_report(pptx_path: Path, evidence_path: Path, content_check_path: Path, office_status: str, visual_status: str) -> dict[str, Any]:
    if office_status not in VALID_CHECK_STATUSES or visual_status not in VALID_CHECK_STATUSES:
        raise ContractError("office and visual statuses must be pass, fail, or not_run")
    evidence = require_object(load_json(evidence_path), "evidence map")
    if evidence.get("format_version") != EVIDENCE_MAP_VERSION:
        raise ContractError(f"evidence map.format_version must be {EVIDENCE_MAP_VERSION!r}")
    _content_report(content_check_path, pptx_path)
    presentation = check_pptx_content(pptx_path)["presentation"]
    checks = {
        "content": {"status": "pass"},
        "evidence_map": {"status": "pass"},
        "office_validation": {"status": office_status},
        "visual_review": {"status": visual_status},
    }
    overall = "pass" if all(item["status"] == "pass" for item in checks.values()) else "fail"
    return {
        "format_version": QA_REPORT_VERSION,
        "presentation": presentation,
        "evidence_map": {"path": str(evidence_path), "sha256": sha256_file(evidence_path)},
        "checks": checks,
        "overall_status": overall,
    }


def validate_report(pptx_path: Path, evidence_path: Path, report_path: Path, allow_incomplete: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        report = require_object(load_json(report_path), "QA report")
        if report.get("format_version") != QA_REPORT_VERSION:
            raise ContractError(f"QA report.format_version must be {QA_REPORT_VERSION!r}")
        presentation = require_object(report.get("presentation"), "QA report.presentation")
        current_report = check_pptx_content(pptx_path)
        if current_report.get("status") != "pass":
            raise ContractError("final PPTX content check does not pass")
        current = current_report["presentation"]
        for key in ("sha256", "slide_count"):
            if presentation.get(key) != current[key]:
                raise ContractError(f"QA report presentation.{key} does not match the final PPTX")
        evidence = require_object(report.get("evidence_map"), "QA report.evidence_map")
        if evidence.get("sha256") != sha256_file(evidence_path):
            raise ContractError("QA report evidence_map.sha256 does not match evidence_map.json")
        checks = require_object(report.get("checks"), "QA report.checks")
        required = {"content", "evidence_map", "office_validation", "visual_review"}
        if set(checks) != required:
            raise ContractError(f"QA report.checks must contain exactly: {', '.join(sorted(required))}")
        statuses = []
        for name in sorted(required):
            check = require_object(checks[name], f"QA report.checks.{name}")
            status = check.get("status")
            if status not in VALID_CHECK_STATUSES:
                raise ContractError(f"QA report.checks.{name}.status is invalid")
            statuses.append(status)
        expected_overall = "pass" if all(status == "pass" for status in statuses) else "fail"
        if report.get("overall_status") != expected_overall:
            raise ContractError("QA report overall_status does not match its check statuses")
        if not allow_incomplete and expected_overall != "pass":
            raise ContractError("QA report is not release-ready: every required check must pass")
    except ContractError as exc:
        errors.append(str(exc))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    validate = subparsers.add_parser("validate")
    for subparser in (create, validate):
        subparser.add_argument("--pptx", type=Path, required=True)
        subparser.add_argument("--evidence-map", type=Path, required=True)
    create.add_argument("--content-check", type=Path, required=True)
    create.add_argument("--office-status", default="not_run", choices=sorted(VALID_CHECK_STATUSES))
    create.add_argument("--visual-status", default="not_run", choices=sorted(VALID_CHECK_STATUSES))
    create.add_argument("--output", type=Path, required=True)
    validate.add_argument("--report", type=Path, required=True)
    validate.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "create":
            write_json(args.output, create_report(args.pptx, args.evidence_map, args.content_check, args.office_status, args.visual_status))
            print(f"Wrote QAReport v1: {args.output}")
            return 0
        errors = validate_report(args.pptx, args.evidence_map, args.report, args.allow_incomplete)
    except ContractError as exc:
        errors = [str(exc)]
    if errors:
        print("QAReport validation FAILED:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("QAReport validation PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
