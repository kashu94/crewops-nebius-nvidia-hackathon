"""CDK entry point. Run it through ``scripts/cdk.sh`` from the repository root.

Settings come from ``.env`` and the environment, exactly as for the application, so the
deployed resource names always match what the code expects. The account comes from the AWS
profile named in ``.env`` (``AWS_PROFILE``); access keys stay in ``~/.aws``, never here.
"""

from __future__ import annotations

import os
import sys

from aws_cdk import App, Environment, Tags
from crewops_infra.alexa_stack import AlexaStack
from crewops_infra.analysis_stack import AnalysisStack
from crewops_infra.auth_stack import AuthStack
from crewops_infra.data_stack import DataStack
from crewops_infra.guardrail_stack import GuardrailStack
from crewops_infra.ingestion_stack import IngestionStack
from crewops_infra.mcp_stack import PACKAGE, McpStack
from crewops_infra.notify_stack import NotifyStack
from crewops_infra.search_stack import SearchStack

from crewops.config import Settings, load_env_file


def main() -> None:
    load_env_file()
    settings = Settings.from_env()
    app = App()
    env = Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=settings.aws_region)

    data = DataStack(app, f"CrewOps-{settings.stage}-Data", settings=settings, env=env)
    ingestion = IngestionStack(
        app,
        f"CrewOps-{settings.stage}-Ingestion",
        settings=settings,
        table=data.table,
        env=env,
    )
    AnalysisStack(
        app,
        f"CrewOps-{settings.stage}-Analysis",
        settings=settings,
        table=data.table,
        env=env,
    )
    SearchStack(app, f"CrewOps-{settings.stage}-Search", settings=settings, env=env)
    guardrail = GuardrailStack(
        app, f"CrewOps-{settings.stage}-Guardrail", settings=settings, env=env
    )
    auth = AuthStack(app, f"CrewOps-{settings.stage}-Auth", settings=settings, env=env)
    notify = NotifyStack(
        app,
        f"CrewOps-{settings.stage}-Notify",
        settings=settings,
        table=data.table,
        auth=auth,
        package=PACKAGE if (PACKAGE / "main.py").exists() else None,
        env=env,
    )
    if (PACKAGE / "main.py").exists():
        McpStack(
            app,
            f"CrewOps-{settings.stage}-Mcp",
            settings=settings,
            table=data.table,
            queue=ingestion.queue,
            auth=auth,
            guardrail=guardrail,
            notify=notify,
            env=env,
        )
        if settings.alexa_skill_id and auth.alexa_client is not None:
            AlexaStack(
                app,
                f"CrewOps-{settings.stage}-Alexa",
                settings=settings,
                table=data.table,
                auth=auth,
                guardrail=guardrail,
                env=env,
            )
    else:
        print(
            "Note: the MCP stack is left out until its package is built "
            "(uv run python -m crewops.mcp.package).",
            file=sys.stderr,
        )

    Tags.of(app).add("project", "crewops")
    Tags.of(app).add("stage", settings.stage)
    app.synth()


if __name__ == "__main__":
    main()
