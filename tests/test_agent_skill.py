"""The CrewOps Agent Skill: valid by the Agent Skills specification, and true to the server
(every tool it calls exists, every rule and strategy it names is the engine's)."""

import asyncio
import json
import re
from pathlib import Path

import pytest

from crewops.engine.options import Strategy
from crewops.ingestion.seed import load_seed
from crewops.mcp import skill_docs
from crewops.mcp.server import build_server
from crewops.tools.schemas import TOOL_NAMES

SKILL = Path(__file__).resolve().parents[1] / "skills" / "crewops-crew-control"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def frontmatter(text: str) -> dict[str, str]:
    """The YAML front matter's top-level keys (enough for the specification's checks)."""
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, "SKILL.md starts with YAML front matter"
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if line and not line.startswith(" ") and ":" in line:
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out


@pytest.fixture(scope="module")
def server_tools():
    return {t.name for t in asyncio.run(build_server(None, None).list_tools())}


def test_the_skill_follows_the_agent_skills_specification():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    meta = frontmatter(text)
    assert meta["name"] == SKILL.name and NAME_RE.match(meta["name"]) and len(meta["name"]) <= 64
    assert "--" not in meta["name"]
    assert 0 < len(meta["description"]) <= 1024
    assert len(meta.get("compatibility", "")) <= 500
    assert meta["license"] == "MIT"
    assert len(text.splitlines()) < 500  # the body stays short; detail is in references/
    for ref in re.findall(r"`((?:references|scripts|assets)/[\w.\-/]+)`", text):
        assert (SKILL / ref).is_file(), ref


def test_the_tool_reference_is_generated_from_the_current_server():
    assert (SKILL / "references" / "tools.md").read_text(encoding="utf-8") == skill_docs.render()


def test_every_tool_the_skill_calls_exists(server_tools):
    for doc in ("SKILL.md", "references/workflows.md", "references/errors.md"):
        text = (SKILL / doc).read_text(encoding="utf-8")
        called = set(re.findall(r"`([a-z_]+)\(", text)) | set(re.findall(r"→ `([a-z_]+)`", text))
        assert called, doc
        assert called <= server_tools, (doc, called - server_tools)
        crew_calls = set(re.findall(r'crew_tool\("([a-z_]+)"', text))
        assert crew_calls <= TOOL_NAMES, (doc, crew_calls - TOOL_NAMES)


def test_rules_and_strategies_named_are_the_engines():
    text = (SKILL / "references" / "legality.md").read_text(encoding="utf-8")
    rule_ids = {r.rule_id for r in load_seed().rules}
    assert set(re.findall(r"RULE-[A-Z]+-\d\d", text)) == rule_ids
    named = set(re.findall(r"^\| `([a-z_]+)` \|", text, re.M))
    assert named == {s.value for s in Strategy}


def test_the_helper_script_and_client_config_parse():
    compile((SKILL / "scripts" / "crewops_mcp.py").read_text(encoding="utf-8"), "s", "exec")
    config = json.loads((SKILL / "assets" / "mcp.json.example").read_text(encoding="utf-8"))
    assert config["mcpServers"]["crewops"]["type"] == "http"
    kiro = json.loads((SKILL / "assets" / "kiro-mcp.json.example").read_text(encoding="utf-8"))
    server = kiro["mcpServers"]["crewops"]
    assert server["headers"]["Authorization"] == "Bearer ${CREWOPS_ACCESS_TOKEN}"
    # Only reads are pre-approved; every change still asks.
    import asyncio as _a

    read_only = {
        t.name
        for t in _a.run(build_server(None, None).list_tools())
        if t.annotations is not None and t.annotations.readOnlyHint
    }
    assert set(server["autoApprove"]) <= read_only
