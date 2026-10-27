import os

import pytest

from crewops.config import ConfigError, Settings, load_env_file, shell_exports


def test_defaults_without_environment():
    s = Settings.from_env({})
    assert s.stage == "dev"
    assert s.aws_region == "ap-southeast-2"
    nemotron = "nvidia/Nemotron-3_5-Lightning"
    assert s.advisor_model_id == s.router_model_id == s.explainer_model_id == nemotron
    assert s.embedding_model_id == "amazon.titan-embed-text-v2:0"
    assert s.nebius_base_url == "https://api.tokenfactory.nebius.com/v1/"
    assert s.nebius_api_key_parameter == "/crewops-dev/nebius/api-key"
    assert s.embedding_dimensions == 512
    assert s.max_tool_iterations == 8
    assert s.guardrails_enabled is False


def test_resource_names_derive_from_stage():
    s = Settings.from_env({"CREWOPS_STAGE": "demo"})
    assert s.table_name == "crewops-demo-ops"
    assert s.vector_bucket == "crewops-demo-vectors"
    assert s.ingest_queue == "crewops-demo-ingest"
    assert s.ingest_dlq == "crewops-demo-ingest-dlq"
    assert s.roster_bucket == "crewops-demo-rosters"
    assert s.alerts_topic == "crewops-demo-alerts"


def test_overrides_are_applied():
    s = Settings.from_env(
        {
            "AWS_REGION": "ap-south-1",
            "CREWOPS_ADVISOR_MODEL_ID": "nvidia/Llama-3_1-Nemotron-Ultra-253B-v1",
            "CREWOPS_NEBIUS_BASE_URL": "https://api.tokenfactory.us-central1.nebius.com/v1",
            "CREWOPS_EMBEDDING_DIMENSIONS": "1024",
            "CREWOPS_GUARDRAIL_ID": "gr-123",
            "CREWOPS_GUARDRAIL_VERSION": "3",
            "CREWOPS_RESPONSE_BUDGET_SECONDS": "5.5",
        }
    )
    assert s.aws_region == "ap-south-1"
    assert s.advisor_model_id == "nvidia/Llama-3_1-Nemotron-Ultra-253B-v1"
    assert s.nebius_base_url == "https://api.tokenfactory.us-central1.nebius.com/v1"
    assert s.embedding_dimensions == 1024
    assert s.guardrails_enabled is True
    assert s.response_budget_seconds == 5.5


@pytest.mark.parametrize("url", ["http://api.tokenfactory.nebius.com/v1/", "api.example.com"])
def test_rejects_a_nebius_url_that_is_not_https(url):
    with pytest.raises(ConfigError, match="CREWOPS_NEBIUS_BASE_URL"):
        Settings.from_env({"CREWOPS_NEBIUS_BASE_URL": url})


def test_falls_back_to_aws_default_region():
    assert Settings.from_env({"AWS_DEFAULT_REGION": "eu-west-1"}).aws_region == "eu-west-1"


def test_empty_values_count_as_unset():
    s = Settings.from_env({"CREWOPS_STAGE": "", "CREWOPS_GUARDRAIL_ID": "  "})
    assert s.stage == "dev"
    assert s.guardrail_id is None


@pytest.mark.parametrize("stage", ["Dev", "1dev", "d", "dev_1", "x" * 21])
def test_rejects_bad_stage(stage):
    with pytest.raises(ConfigError, match="CREWOPS_STAGE"):
        Settings.from_env({"CREWOPS_STAGE": stage})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("CREWOPS_EMBEDDING_DIMENSIONS", "768"),
        ("CREWOPS_MAX_TOOL_ITERATIONS", "0"),
        ("CREWOPS_MAX_TOOL_ITERATIONS", "eight"),
        ("CREWOPS_RESPONSE_BUDGET_SECONDS", "-1"),
        ("CREWOPS_RESPONSE_BUDGET_SECONDS", "soon"),
    ],
)
def test_rejects_bad_numbers(key, value):
    with pytest.raises(ConfigError, match=key):
        Settings.from_env({key: value})


def test_settings_are_immutable():
    s = Settings.from_env({})
    with pytest.raises(AttributeError):
        s.stage = "prod"


@pytest.fixture
def private_environ(monkeypatch):
    """A throwaway copy of the environment, so .env loading cannot leak into other tests."""
    env = dict(os.environ)
    for key in [k for k in env if k.startswith(("CREWOPS_", "AWS_"))]:
        del env[key]
    monkeypatch.setattr(os, "environ", env)
    return env


def test_load_env_file_applies_values_without_overriding_shell(tmp_path, private_environ):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "\n"
        "CREWOPS_STAGE=demo\n"
        "export AWS_REGION='eu-west-1'\n"
        'CREWOPS_GUARDRAIL_ID="gr-1"\n'
        "CREWOPS_GUARDRAIL_VERSION=1\n"
        "CREWOPS_MAX_TOOL_ITERATIONS=5  # inline comment\n"
        "AWS_PROFILE=fromfile\n"
    )
    private_environ["AWS_PROFILE"] = "fromshell"

    applied = load_env_file(env)

    assert private_environ["CREWOPS_STAGE"] == "demo"
    assert private_environ["AWS_REGION"] == "eu-west-1"
    assert private_environ["CREWOPS_GUARDRAIL_ID"] == "gr-1"
    assert private_environ["CREWOPS_MAX_TOOL_ITERATIONS"] == "5"
    assert private_environ["AWS_PROFILE"] == "fromshell"
    assert "AWS_PROFILE" not in applied
    assert Settings.from_env().stage == "demo"


@pytest.mark.parametrize(
    ("line", "value"),
    [
        ("A=crewops  ", "crewops"),
        ("A=crewops\r", "crewops"),
        ("A = crewops", "crewops"),
        ("export   A=crewops", "crewops"),
        ('A="a b" # note', "a b"),
        ("A='x#y'", "x#y"),
        ("A=a=b#c", "a=b#c"),
        ("A=plain # note", "plain"),
        ("A=", ""),
        ("A= # none yet", ""),
        ("A=\t# none yet", ""),
        ("A=#x", "#x"),
    ],
)
def test_env_value_edge_cases(tmp_path, private_environ, line, value):
    env = tmp_path / ".env"
    env.write_bytes((line + "\n").encode())
    load_env_file(env)
    assert private_environ["A"] == value


@pytest.mark.parametrize("line", ["T", 'A="unterminated', 'A="x" trailing'])
def test_env_rejects_malformed_lines(tmp_path, private_environ, line):
    env = tmp_path / ".env"
    env.write_text(line + "\n")
    with pytest.raises(ConfigError, match=r"\.env:1"):
        load_env_file(env)


def test_shell_exports_resolve_region_and_stage_like_python(tmp_path, private_environ):
    env = tmp_path / ".env"
    env.write_text("AWS_PROFILE=crewops\nX='a b'\n")
    private_environ["AWS_DEFAULT_REGION"] = "eu-west-1"

    lines = shell_exports(env).splitlines()

    assert "export AWS_PROFILE=crewops" in lines
    assert "export AWS_REGION=eu-west-1" in lines  # same fallback the Python code uses
    assert "export CREWOPS_STAGE=dev" in lines
    assert "export X='a b'" in lines  # quoted safely for the shell


def test_load_env_file_missing_is_fine(tmp_path, private_environ):
    assert load_env_file(tmp_path / "nope.env") == {}


def test_load_env_file_rejects_garbage(tmp_path, private_environ):
    bad = tmp_path / ".env"
    bad.write_text("this is not a setting\n")
    with pytest.raises(ConfigError, match=r"\.env:1"):
        load_env_file(bad)


def test_router_semantic_floor():
    assert Settings.from_env({}).router_semantic_floor == 0.45
    assert Settings.from_env({"CREWOPS_ROUTER_SEMANTIC_FLOOR": "0.3"}).router_semantic_floor == 0.3
    for bad in ("1", "1.5", "0", "-0.2", "high"):
        with pytest.raises(ConfigError, match="CREWOPS_ROUTER_SEMANTIC_FLOOR"):
            Settings.from_env({"CREWOPS_ROUTER_SEMANTIC_FLOOR": bad})


def test_guardrail_is_pinned_to_a_published_version():
    s = Settings.from_env({"CREWOPS_GUARDRAIL_ID": "gr-1", "CREWOPS_GUARDRAIL_VERSION": "2"})
    assert s.guardrails_enabled and s.guardrail_version == "2"
    with pytest.raises(ConfigError, match="CREWOPS_GUARDRAIL_VERSION must be set"):
        Settings.from_env({"CREWOPS_GUARDRAIL_ID": "gr-1"})
    with pytest.raises(ConfigError, match="not DRAFT"):
        Settings.from_env({"CREWOPS_GUARDRAIL_ID": "gr-1", "CREWOPS_GUARDRAIL_VERSION": "DRAFT"})


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_numbers_must_be_finite(value):
    with pytest.raises(ConfigError, match="finite"):
        Settings.from_env({"CREWOPS_RESPONSE_BUDGET_SECONDS": value})
    with pytest.raises(ConfigError, match="CREWOPS_ROUTER_SEMANTIC_FLOOR"):
        Settings.from_env({"CREWOPS_ROUTER_SEMANTIC_FLOOR": value})


@pytest.mark.parametrize(
    "key", ["CREWOPS_ROUTER_MODEL_ID", "CREWOPS_ADVISOR_MODEL_ID", "CREWOPS_EXPLAINER_MODEL_ID"]
)
@pytest.mark.parametrize("model", ["amazon.nova-micro-v1:0", "global.amazon.nova-2-lite-v1:0"])
def test_a_bedrock_id_left_in_env_is_refused_for_the_chat_roles(key, model):
    with pytest.raises(ConfigError, match="Bedrock model id"):
        Settings.from_env({key: model})


def test_token_factory_ids_are_accepted():
    s = Settings.from_env({"CREWOPS_ADVISOR_MODEL_ID": "meta-llama/Meta-Llama-3.1-70B-Instruct"})
    assert s.advisor_model_id == "meta-llama/Meta-Llama-3.1-70B-Instruct"


def test_a_windows_env_file_with_a_byte_order_mark_reads_normally(tmp_path, private_environ):
    env = tmp_path / ".env"
    env.write_bytes("AWS_PROFILE=crewops\r\nCREWOPS_STAGE=demo\r\n".encode("utf-8-sig"))
    assert load_env_file(env) == {"AWS_PROFILE": "crewops", "CREWOPS_STAGE": "demo"}


def test_the_nebius_key_stays_out_of_the_deploy_environment(tmp_path, private_environ):
    private_environ.pop("NEBIUS_API_KEY", None)
    private_environ.pop("CREWOPS_NEBIUS_API_KEY", None)
    env = tmp_path / ".env"
    env.write_text("AWS_PROFILE=crewops\nNEBIUS_API_KEY=nb-secret\nCREWOPS_NEBIUS_API_KEY=nb-2\n")
    exports = shell_exports(env)
    assert "nb-secret" not in exports and "nb-2" not in exports
    assert "export AWS_PROFILE=crewops" in exports.splitlines()


def test_env_example_loads_as_documented(private_environ):
    """Copying .env.example to .env must give working settings, not a config error."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / ".env.example"
    load_env_file(example)
    s = Settings.from_env()
    assert s.stage == "nebius" and s.guardrails_enabled is False


def test_nebius_reasoning_is_off_by_default_and_on_or_off_only():
    assert Settings.from_env({}).nebius_reasoning is False
    assert Settings.from_env({"CREWOPS_NEBIUS_REASONING": "on"}).nebius_reasoning is True
    with pytest.raises(ConfigError, match="CREWOPS_NEBIUS_REASONING"):
        Settings.from_env({"CREWOPS_NEBIUS_REASONING": "maybe"})
