"""Write the CrewOps Agent Skill's tool reference from the server itself.

    uv run python -m crewops.mcp.skill_docs          # rewrites the reference
    uv run python -m crewops.mcp.skill_docs --check  # exits 1 if it is out of date

The skill (``skills/crewops-crew-control``) teaches an agent to use the MCP server. Its tool
reference is generated, never written by hand: every MCP tool with its parameters, whether
it changes anything, and its description, then the deterministic crew tools reachable
through ``crew_tool`` and which roles may call each. A test fails when the server changes
and the reference was not regenerated, so the skill cannot drift from the code.
"""

from __future__ import annotations

import asyncio
import sys
from inspect import cleandoc
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = ROOT / "skills" / "crewops-crew-control" / "references" / "tools.md"

HEADER = """\
# CrewOps MCP tools

Generated from the server by `python -m crewops.mcp.skill_docs`; do not edit by hand.

Every call runs as the signed-in person: the server reads who they are from the access
token, never from the arguments, and refuses what their role may not do (`FORBIDDEN: ...`).
**Changes** marks tools that change something (a report, a plan, the roster, a setting);
**commits** marks the ones that cannot be undone by the same person (approving a plan
rewrites the roster). Ask the person before calling either.
"""


def _effect(annotations: Any) -> str:
    if annotations is None or getattr(annotations, "readOnlyHint", None):
        return "reads only"
    if getattr(annotations, "destructiveHint", None):
        return "**commits**"
    return "**changes**"


def _params(schema: dict[str, Any]) -> list[str]:
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    out = []
    for name, spec in props.items():
        kind = spec.get("type") or "/".join(
            sorted({str(s.get("type")) for s in spec.get("anyOf", []) if s.get("type")})
        )
        default = f", default `{spec['default']!r}`" if "default" in spec else ""
        need = "required" if name in required else "optional"
        out.append(f"`{name}` ({kind or 'any'}, {need}{default})")
    return out


def render() -> str:
    from crewops.domain import Role
    from crewops.mcp.server import build_server
    from crewops.tools.dispatch import ROLE_TOOLS
    from crewops.tools.schemas import TOOL_SCHEMAS

    tools = asyncio.run(build_server(None, None).list_tools())  # type: ignore[arg-type]
    lines = [HEADER, "## Server tools", ""]
    for t in sorted(tools, key=lambda t: t.name):
        params = _params(t.inputSchema)
        lines.append(f"### `{t.name}`: {_effect(t.annotations)}")
        lines.append("")
        lines.append(" ".join(cleandoc(t.description or "").split()))
        lines.append("")
        lines.append("Parameters: " + ("; ".join(params) if params else "none") + ".")
        lines.append("")

    lines += [
        "## Crew tools (through `crew_tool`)",
        "",
        "Deterministic: structured rows and verdicts from the roster and the legality",
        "engine, no model involved. Call `list_crew_tools` for the exact input schemas the",
        "signed-in person may use; `crew_tool(name, arguments)` calls one.",
        "",
        "| Tool | Who may call it | What it does |",
        "|---|---|---|",
    ]
    roles = [r for r in Role if r is not Role.SPEAKER]
    for t in TOOL_SCHEMAS:
        spec = t.get("toolSpec", t)
        name = spec["name"]
        who = ", ".join(r.value for r in roles if name in ROLE_TOOLS.get(r, frozenset()))
        what = " ".join((spec.get("description") or "").split())
        first = what.split(". ")[0].rstrip(".") + "."
        lines.append(f"| `{name}` | {who or 'none'} | {first} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = render()
    if "--check" in args:
        if not REFERENCE.exists() or REFERENCE.read_text(encoding="utf-8") != text:
            print(f"{REFERENCE} is out of date: run python -m crewops.mcp.skill_docs")
            return 1
        print("The skill's tool reference matches the server.")
        return 0
    REFERENCE.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE.write_text(text, encoding="utf-8")
    print(f"Wrote {REFERENCE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
