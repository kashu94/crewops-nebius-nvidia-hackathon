"""Build the MCP server's code package for AgentCore Runtime.

    uv run python -m crewops.mcp.package

AgentCore runs code packages on Linux ARM64 and does not install dependencies, so the
package carries them: the server's locked dependencies as ARM64 (aarch64 manylinux) wheels,
this repository's ``crewops`` code, the example questions the router learns from, and a
``main.py`` that starts the server. The result goes to ``build/mcp_server``, which the MCP
stack deploys. Run it again after changing the code; the stack redeploys when the package's
contents change.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "build" / "mcp_server"
PYTHON_VERSION = "3.12"
"""Must match the stack's AgentCore runtime (PYTHON_3_12)."""
PLATFORM = "aarch64-manylinux2014"
MAX_BYTES = 750 * 1024 * 1024
"""AgentCore's limit for an unzipped code package."""

MAIN = '''"""Entry point AgentCore runs: the CrewOps MCP server on 0.0.0.0:8000/mcp."""

import os
from pathlib import Path

os.environ.setdefault("CREWOPS_DATA_DIR", str(Path(__file__).resolve().parent / "data"))

from crewops.mcp.server import main  # noqa: E402

main(host="0.0.0.0")  # AgentCore reaches the server from outside its sandbox
'''


def _uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is needed to build the package (https://docs.astral.sh/uv/)")
    return uv


def build(out: Path = OUT) -> Path:
    uv = _uv()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    requirements = out.parent / "mcp_server_requirements.txt"
    subprocess.run(
        [
            uv,
            "export",
            "--frozen",
            "--no-default-groups",
            "--group",
            "server",
            "--no-emit-project",
            "--no-hashes",
            "--format",
            "requirements-txt",
            "--output-file",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python-platform",
            PLATFORM,
            "--python-version",
            PYTHON_VERSION,
            "--only-binary=:all:",
            "--target",
            str(out),
            "-r",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
    )
    shutil.copytree(
        ROOT / "src" / "crewops",
        out / "crewops",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (out / "data" / "eval").mkdir(parents=True)
    shutil.copy2(ROOT / "data" / "eval" / "questions.json", out / "data" / "eval")
    (out / "main.py").write_text(MAIN, encoding="utf-8")
    _tidy(out)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    if size > MAX_BYTES:
        raise SystemExit(f"package is {size / 2**20:.0f} MB, over AgentCore's 750 MB")
    return out


def _tidy(out: Path) -> None:
    """No compiled caches; files 644 and directories 755, as AgentCore expects."""
    for cache in list(out.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    for path in out.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.suffix in (".pyc",):
            path.unlink()
        else:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)


def main(argv: list[str] | None = None) -> int:
    out = build()
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"Built {out.relative_to(ROOT)} ({size / 2**20:.1f} MB). Deploy the MCP stack next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
