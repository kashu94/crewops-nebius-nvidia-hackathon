"""The tools the agent and the MCP server call: lookups, legality, cover, what-ifs.

``CrewOpsTools`` implements them over one loaded operation; ``dispatch`` runs a call for a
signed-in caller, checking their role and the arguments first; ``TOOL_SCHEMAS`` describes
them to a model.
"""

from crewops.tools.dispatch import Caller, TraceEntry, allowed_tools, dispatch, validate_args
from crewops.tools.errors import ToolError
from crewops.tools.port import CrewOpsTools
from crewops.tools.schemas import TOOL_NAMES, TOOL_SCHEMAS, bedrock_tool_config

__all__ = [
    "TOOL_NAMES",
    "TOOL_SCHEMAS",
    "Caller",
    "CrewOpsTools",
    "ToolError",
    "TraceEntry",
    "allowed_tools",
    "bedrock_tool_config",
    "dispatch",
    "validate_args",
]
