from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.services.agentic.runner import bwrap_available, build_bwrap_launch_args


@pytest.mark.skipif(not bwrap_available(), reason="bubblewrap is not installed")
def test_restricted_bwrap_exposes_only_stage_mounts(tmp_path: Path) -> None:
    workspace = tmp_path / "jobs" / "job-1"
    evidence = workspace / "work" / "evidence.json"
    output = workspace / "output"
    reviewer_skill = workspace / ".agents" / "skills" / "semantic-slide-review"
    hidden_input = workspace / "input"
    sibling_secret = tmp_path / "jobs" / "another-job" / "secret.txt"
    backend_root = tmp_path / "backend"

    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}", encoding="utf-8")
    output.mkdir(parents=True)
    reviewer_skill.mkdir(parents=True)
    (reviewer_skill / "SKILL.md").write_text("review", encoding="utf-8")
    hidden_input.mkdir()
    (hidden_input / "source.pdf").write_bytes(b"source")
    sibling_secret.parent.mkdir(parents=True)
    sibling_secret.write_text("secret", encoding="utf-8")
    backend_skill = backend_root / ".agents" / "skills" / "pptx-nhi-tw"
    backend_skill.mkdir(parents=True)
    (backend_skill / "SKILL.md").write_text("author", encoding="utf-8")

    args = list(
        build_bwrap_launch_args(
            workspace,
            codex_bin=Path("/usr/bin/true"),
            writable=True,
            backend_root=backend_root,
            skill_names=("semantic-slide-review",),
            read_only_paths=(evidence,),
            writable_paths=(output,),
            restrict_workspace=True,
        )
    )
    # Replace the Codex app-server command with a direct namespace probe.
    del args[-4:]
    args.extend(
        (
            "/usr/bin/sh",
            "-c",
            " && ".join(
                (
                    f"test -r '{evidence}'",
                    f"test -r '{reviewer_skill / 'SKILL.md'}'",
                    f"printf ok > '{output / 'probe.txt'}'",
                    f"test ! -e '{hidden_input}'",
                    f"test ! -e '{sibling_secret}'",
                    f"test ! -e '{backend_root / '.agents' / 'skills'}'",
                )
            ),
        )
    )

    completed = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert (output / "probe.txt").read_text(encoding="utf-8") == "ok"
