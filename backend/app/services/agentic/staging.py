"""Safe staging of declared skills into an agent workspace."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Iterable


class SkillStagingError(ValueError):
    """Raised before Codex when a declared skill is not allowlisted on disk."""


_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_skill_name(name: str) -> str:
    if not isinstance(name, str) or not _SKILL_NAME.fullmatch(name):
        raise SkillStagingError("workflow declares an invalid skill")
    return name


def stage_declared_skills(
    workspace: Path,
    declared_skills: Iterable[str],
    *,
    skills_root: Path | None = None,
) -> list[str]:
    """Copy only known ``backend/.agents/skills/<name>`` trees.

    The source is resolved and checked to remain below ``skills_root`` and to
    contain ``SKILL.md``.  The destination is recreated from source files, so
    a task cannot smuggle an arbitrary path or script through its payload.
    """

    workspace_path = Path(workspace)
    if workspace_path.is_symlink():
        raise SkillStagingError("workflow workspace is invalid")
    workspace = workspace_path.resolve()
    root = (skills_root or Path(__file__).resolve().parents[3] / ".agents" / "skills").resolve()
    names = [validate_skill_name(name) for name in declared_skills]
    if len(names) != len(set(names)):
        raise SkillStagingError("workflow declares duplicate skills")
    destination_root = workspace / ".agents" / "skills"
    if destination_root.is_symlink():
        raise SkillStagingError("workflow skill destination is invalid")
    destination_root.mkdir(parents=True, exist_ok=True)
    # A retried job may reuse a retained workspace.  Remove any stale skill
    # trees before copying the current adapter's exact allowlist.
    for existing in destination_root.iterdir():
        if existing.name in names:
            continue
        if existing.is_symlink() or existing.is_file():
            existing.unlink()
        elif existing.is_dir():
            shutil.rmtree(existing)
    staged: list[str] = []
    for name in names:
        source = (root / name).resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise SkillStagingError("workflow declares an invalid skill path") from exc
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            raise SkillStagingError(f"workflow skill is unknown or invalid: {name}")
        destination = destination_root / name
        if destination.is_symlink():
            raise SkillStagingError("workflow skill destination is invalid")
        shutil.copytree(source, destination, dirs_exist_ok=True)
        staged.append(name)
    return staged
