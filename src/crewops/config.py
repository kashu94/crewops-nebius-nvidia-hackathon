"""Runtime settings, read once from the environment.

All AWS resource names hang off the stage name, so ``dev``, ``demo`` and ``prod`` can live
side by side and no name is typed in two places. This module never calls AWS or Nebius, it
only works out which resources and models everything else uses.

Empty env vars count as unset, so ``CREWOPS_GUARDRAIL_ID=`` in ``.env`` means "no guardrail
yet" rather than a guardrail called ''.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

STAGE_RE = re.compile(r"^[a-z][a-z0-9-]{1,19}$")
"""Kept short and lowercase because some resource names (S3 vector buckets, for one) only
take lowercase letters, digits and hyphens."""

TITAN_V2_DIMENSIONS = (256, 512, 1024)

DEFAULT_REGION = "ap-southeast-2"
"""Sydney. Everything we use on AWS is available there, including S3 Vectors, AgentCore,
Bedrock Guardrails and the Titan embedding model."""

# The three chat roles (triage, advisor, explainer) run NVIDIA Llama-3.1-Nemotron-70B-Instruct
# on Nebius Token Factory, through its OpenAI-compatible API (crewops.agent.nebius). Any
# Token Factory model with tool calling can be swapped in through the environment; check
# what a key can use with ``python -m crewops.agent.nebius``.
DEFAULT_NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
DEFAULT_ROUTER_MODEL = "nvidia/Llama-3.1-Nemotron-70B-Instruct-HF"
DEFAULT_ADVISOR_MODEL = "nvidia/Llama-3.1-Nemotron-70B-Instruct-HF"
DEFAULT_EXPLAINER_MODEL = "nvidia/Llama-3.1-Nemotron-70B-Instruct-HF"
# Embeddings stay on Bedrock: the S3 Vectors indexes were built with Titan V2, and a query
# must be embedded by the model that built the index.
DEFAULT_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"


class ConfigError(ValueError):
    """Setting is present but unusable."""


ENV_LINE_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _parse_value(value: str, where: str) -> str:
    """``"a b" # note`` -> ``a b``; ``plain # note`` -> ``plain``; ``a=b#c`` -> ``a=b#c``."""
    if value[:1] in ('"', "'"):
        quote = value[0]
        end = value.find(quote, 1)
        if end == -1:
            raise ConfigError(f"{where}: unterminated quote in {value!r}")
        rest = value[end + 1 :].strip()
        if rest and not rest.startswith("#"):
            raise ConfigError(f"{where}: unexpected text after the quoted value: {rest!r}")
        return value[1:end]
    if " #" in value or "\t#" in value:
        value = re.split(r"\s#", value, maxsplit=1)[0]
    return value.rstrip()


def load_env_file(path: str | os.PathLike[str] = ".env", override: bool = False) -> dict[str, str]:
    """Load ``KEY=VALUE`` lines from ``.env`` into ``os.environ``. Returns what was applied.

    Handles comments, an optional ``export`` prefix, spaces around ``=``, CRLF endings,
    quoted values, and trailing ``# note``. Shell variables win unless ``override`` is set,
    so a one-off ``CREWOPS_STAGE=demo ...`` beats the file. No file is fine since every
    setting has a default.
    """
    applied: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return applied

    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ENV_LINE_RE.match(line)
        if not match:
            raise ConfigError(f"{path}:{number}: expected KEY=VALUE, got {line!r}")
        key, value = match.group(1), _parse_value(match.group(2).strip(), f"{path}:{number}")
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


def _get(env: Mapping[str, str], key: str, default: str | None = None) -> str | None:
    value = env.get(key)
    if value is None or not value.strip():
        return default
    return value.strip()


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 1) -> int:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}, got {value}")
    return value


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{key} must be > 0, got {value}")
    return value


def _guardrail_version(env: Mapping[str, str]) -> str:
    """Guardrails must be pinned to a published version. DRAFT can be edited in the console
    and would change behaviour under a running service without a deploy."""
    version = _get(env, "CREWOPS_GUARDRAIL_VERSION")
    if _get(env, "CREWOPS_GUARDRAIL_ID") and not version:
        raise ConfigError(
            "CREWOPS_GUARDRAIL_VERSION must be set with CREWOPS_GUARDRAIL_ID: use the "
            "guardrail stack's GuardrailVersionNumber output"
        )
    if version and version.upper() == "DRAFT":
        raise ConfigError("CREWOPS_GUARDRAIL_VERSION must be a published version, not DRAFT")
    return version or ""


def _senders(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = _get(env, "CREWOPS_QUEUE_SENDERS") or ""
    senders = tuple(s.strip() for s in raw.split(",") if s.strip())
    for arn in senders:
        if not re.match(r"^arn:aws[a-z-]*:iam::\d{12}:(user|role)/[\w+=,.@/-]+$", arn):
            raise ConfigError(f"CREWOPS_QUEUE_SENDERS: {arn!r} is not an IAM user or role ARN")
    return senders


def _redirects(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = _get(env, "CREWOPS_ALEXA_REDIRECT_URLS") or ""
    urls = tuple(u.strip() for u in raw.split(",") if u.strip())
    pattern = r"^https://[a-z0-9.-]+\.amazon\.(com|co\.jp)/api/skill/link/[A-Z0-9]+$"
    for url in urls:
        if not re.match(pattern, url):
            raise ConfigError(
                f"CREWOPS_ALEXA_REDIRECT_URLS: {url!r} is not an Alexa account-linking redirect URL"
            )
    return urls


def _events_stage(env: Mapping[str, str]) -> str:
    stage = _get(env, "CREWOPS_ALEXA_EVENTS_STAGE", "development")
    if stage not in ("development", "live"):
        raise ConfigError(f"CREWOPS_ALEXA_EVENTS_STAGE must be development or live, got {stage!r}")
    return stage


def _base_url(env: Mapping[str, str]) -> str:
    url = _get(env, "CREWOPS_NEBIUS_BASE_URL", DEFAULT_NEBIUS_BASE_URL)
    if not re.match(r"^https://[A-Za-z0-9.-]+(:[0-9]+)?(/[^\s]*)?$", url):
        raise ConfigError(f"CREWOPS_NEBIUS_BASE_URL must be an https URL, got {url!r}")
    return url


def _fraction(env: Mapping[str, str], key: str, default: float) -> float:
    value = _float(env, key, default)
    if value >= 1:
        raise ConfigError(f"{key} must be below 1, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    stage: str
    aws_region: str

    router_model_id: str
    """Triage, advisor and explainer: Nebius Token Factory model ids."""
    advisor_model_id: str
    explainer_model_id: str
    embedding_model_id: str
    """Bedrock model id."""
    embedding_dimensions: int

    guardrail_id: str | None
    guardrail_version: str

    max_tool_iterations: int
    """Hard stop on the advisor's tool loop."""
    router_semantic_floor: float
    """Minimum cosine similarity for the router to reuse the closest example's intent.
    Anything lower goes to the triage model. Tuned on real paraphrases with
    ``python -m crewops.agent.calibrate``."""

    user_pool_id: str | None
    """Cognito pool and app client whose tokens the MCP server accepts. The stack sets
    these on the deployed server, local commands read them from the auth stack."""
    user_pool_client_id: str | None
    ingest_queue_url: str | None
    queue_senders: tuple[str, ...]
    """Extra IAM principals allowed to send straight to the queue, e.g. an operator testing
    with ``send_report``. Empty means only the MCP server."""
    memory_id: str | None
    """AgentCore Memory for recent conversation turns. Unset means each question is
    answered on its own."""
    alerts_topic_arn: str | None
    events_http_domain: str | None
    """AppSync Events HTTP domain for live console updates."""
    response_budget_seconds: float
    """Alexa gives us 8 s. The default leaves some margin."""
    alexa_skill_id: str | None = None
    """amzn1.ask.skill... id. Requests for any other skill are refused. Unset means no skill
    is deployed."""
    alexa_redirect_urls: tuple[str, ...] = ()
    """From the skill's Account Linking page. When set, the auth stack adds a hosted sign-in
    page and an app client for Alexa."""
    alexa_client_id: str | None = None
    """Cognito app client used for Alexa account linking. Set on the skill by its stack."""
    alexa_events_stage: str = "development"
    """In development, events only reach the developer's own devices. Use "live" once the
    skill is certified."""
    escalate_after_minutes: int = 10
    """Untouched-on-desk time before the duty supervisor is emailed and speakers announce."""
    escalation_function_arn: str | None = None
    escalation_role_arn: str | None = None
    """Escalation Lambda and the role Scheduler invokes it with. Both are set on the notifier
    by its stack. If either is missing no escalations get scheduled."""
    push_enabled: bool = False
    """The stack only turns this on when the notifier ships the MCP package, because that's
    the one bundling ``cryptography`` for web push."""
    nebius_base_url: str = DEFAULT_NEBIUS_BASE_URL
    """Token Factory's OpenAI-compatible API. The key is never a setting: it comes from
    ``NEBIUS_API_KEY`` locally or from Parameter Store (``nebius_api_key_parameter``)."""

    # -- Derived resource names ---------------------------------------------

    @property
    def prefix(self) -> str:
        return f"crewops-{self.stage}"

    @property
    def table_name(self) -> str:
        """Single-table design, the whole operation lives here."""
        return f"{self.prefix}-ops"

    @property
    def vector_bucket(self) -> str:
        return f"{self.prefix}-vectors"

    @property
    def rules_index(self) -> str:
        return "rules"

    @property
    def intents_index(self) -> str:
        return "intents"

    @property
    def ingest_queue(self) -> str:
        return f"{self.prefix}-ingest"

    @property
    def ingest_dlq(self) -> str:
        return f"{self.prefix}-ingest-dlq"

    @property
    def roster_bucket(self) -> str:
        return f"{self.prefix}-rosters"

    @property
    def alerts_topic(self) -> str:
        """Outbound only. Nothing reads from this topic."""
        return f"{self.prefix}-alerts"

    @property
    def event_api_name(self) -> str:
        return f"{self.prefix}-events"

    @property
    def memory_name(self) -> str:
        # AgentCore Memory names don't allow hyphens
        return f"crewops_{self.stage.replace('-', '_')}_memory"

    @property
    def issuer(self) -> str | None:
        """Expected ``iss`` claim on the pool's tokens."""
        if not self.user_pool_id:
            return None
        return f"https://cognito-idp.{self.aws_region}.amazonaws.com/{self.user_pool_id}"

    @property
    def mcp_runtime_name(self) -> str:
        # same naming rule as memory_name
        return f"crewops_{self.stage.replace('-', '_')}_mcp"

    @property
    def mcp_runtime_role(self) -> str:
        """Fixed name so the ingest queue policy can reference it without a dependency on
        the MCP stack."""
        return f"{self.prefix}-mcp-runtime"

    @property
    def user_pool_name(self) -> str:
        """Roles are Cognito groups in this pool."""
        return f"{self.prefix}-users"

    @property
    def guardrail_name(self) -> str:
        return f"{self.prefix}-guardrail"

    @property
    def alexa_parameter_prefix(self) -> str:
        """Holds the skill's LWA client id and secret for sending notifications. The secret
        is a SecureString created by hand."""
        return f"/{self.prefix}/alexa"

    @property
    def push_parameter_prefix(self) -> str:
        """VAPID keys. Private is a SecureString, public is plain so the web app can read it."""
        return f"/{self.prefix}/push"

    @property
    def nebius_api_key_parameter(self) -> str:
        """SecureString holding the Nebius Token Factory API key, created by hand."""
        return f"/{self.prefix}/nebius/api-key"

    @property
    def guardrails_enabled(self) -> bool:
        return self.guardrail_id is not None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        stage = _get(env, "CREWOPS_STAGE", "dev")
        if not STAGE_RE.match(stage):
            raise ConfigError(
                f"CREWOPS_STAGE must be 2-20 lowercase letters, digits or hyphens, "
                f"starting with a letter; got {stage!r}"
            )

        dims = _int(env, "CREWOPS_EMBEDDING_DIMENSIONS", 512)
        if dims not in TITAN_V2_DIMENSIONS:
            raise ConfigError(
                f"CREWOPS_EMBEDDING_DIMENSIONS must be one of {TITAN_V2_DIMENSIONS}, got {dims}"
            )

        return cls(
            stage=stage,
            aws_region=_get(env, "AWS_REGION") or _get(env, "AWS_DEFAULT_REGION", DEFAULT_REGION),
            router_model_id=_get(env, "CREWOPS_ROUTER_MODEL_ID", DEFAULT_ROUTER_MODEL),
            advisor_model_id=_get(env, "CREWOPS_ADVISOR_MODEL_ID", DEFAULT_ADVISOR_MODEL),
            explainer_model_id=_get(env, "CREWOPS_EXPLAINER_MODEL_ID", DEFAULT_EXPLAINER_MODEL),
            embedding_model_id=_get(env, "CREWOPS_EMBEDDING_MODEL_ID", DEFAULT_EMBEDDING_MODEL),
            embedding_dimensions=dims,
            guardrail_id=_get(env, "CREWOPS_GUARDRAIL_ID"),
            guardrail_version=_guardrail_version(env),
            max_tool_iterations=_int(env, "CREWOPS_MAX_TOOL_ITERATIONS", 8),
            router_semantic_floor=_fraction(env, "CREWOPS_ROUTER_SEMANTIC_FLOOR", 0.45),
            user_pool_id=_get(env, "CREWOPS_USER_POOL_ID"),
            user_pool_client_id=_get(env, "CREWOPS_USER_POOL_CLIENT_ID"),
            ingest_queue_url=_get(env, "CREWOPS_INGEST_QUEUE_URL"),
            queue_senders=_senders(env),
            memory_id=_get(env, "CREWOPS_MEMORY_ID"),
            alerts_topic_arn=_get(env, "CREWOPS_ALERTS_TOPIC_ARN"),
            events_http_domain=_get(env, "CREWOPS_EVENTS_HTTP_DOMAIN"),
            response_budget_seconds=_float(env, "CREWOPS_RESPONSE_BUDGET_SECONDS", 6.0),
            alexa_skill_id=_get(env, "CREWOPS_ALEXA_SKILL_ID"),
            alexa_redirect_urls=_redirects(env),
            alexa_client_id=_get(env, "CREWOPS_ALEXA_CLIENT_ID"),
            alexa_events_stage=_events_stage(env),
            escalate_after_minutes=_int(env, "CREWOPS_ESCALATE_AFTER_MINUTES", 10),
            escalation_function_arn=_get(env, "CREWOPS_ESCALATION_FUNCTION_ARN"),
            escalation_role_arn=_get(env, "CREWOPS_ESCALATION_ROLE_ARN"),
            push_enabled=_get(env, "CREWOPS_PUSH_ENABLED") == "1",
            nebius_base_url=_base_url(env),
        )


def shell_exports(path: str | os.PathLike[str] = ".env") -> str:
    """``export`` lines with the ``.env`` values plus resolved region and stage.
    ``scripts/cdk.sh`` evals this so the shell and Python share one ``.env`` parser and
    the same defaults.
    """
    import shlex

    applied = load_env_file(path)
    settings = Settings.from_env()
    values = {**applied, "AWS_REGION": settings.aws_region, "CREWOPS_STAGE": settings.stage}
    return "\n".join(f"export {key}={shlex.quote(value)}" for key, value in values.items())


if __name__ == "__main__":
    print(shell_exports())
