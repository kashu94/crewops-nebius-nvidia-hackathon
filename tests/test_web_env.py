"""The web app's settings file, built from stack outputs."""

from crewops.web_env import lines


def test_settings_come_from_the_stack_outputs():
    out = lines(
        "ap-southeast-2",
        {"UserPoolClientId": "client-1"},
        {"McpRuntimeArn": "arn:aws:bedrock-agentcore:ap-southeast-2:111111111111:runtime/x"},
        {"EventsHttpDomain": "h.example", "EventsRealtimeDomain": "r.example"},
        "crewops",
    )
    assert "CREWOPS_USER_POOL_CLIENT_ID=client-1" in out
    assert "CREWOPS_EVENTS_REALTIME_DOMAIN=r.example" in out
    assert out[-1] == "AWS_PROFILE=crewops"
    assert not any("SECRET" in line or "PASSWORD" in line for line in out)


def test_speaker_settings_are_its_own_random_secrets_and_kept(tmp_path):
    import pytest

    from crewops.auth.provision import password_problems
    from crewops.web_env import kept_speaker_lines, new_secret, speaker_lines

    passwords = {new_secret(24, symbol=True) for _ in range(20)}
    assert len(passwords) == 20  # random each time
    for pw in passwords:
        assert password_problems(pw) == []  # the pool accepts it
        assert not set(pw) & set("#$'\" =")  # kept exactly in a .env file, unquoted
    code = new_secret(10, symbol=False)
    assert len(code) == 10 and code.isalnum()
    assert speaker_lines(next(iter(passwords)), code)[2] == f"CREWOPS_SPEAKER_PAIRING_CODE={code}"
    for bad in ("Ab1#xyz", "Ab1$HOME", ""):
        with pytest.raises(ValueError):
            speaker_lines(bad, code)
    env = tmp_path / ".env.local"
    env.write_text(
        "CREWOPS_REGION=ap-southeast-2\nCREWOPS_SPEAKER_PASSWORD=Pw-1\nAWS_PROFILE=crewops\n"
    )
    assert kept_speaker_lines(env) == ["CREWOPS_SPEAKER_PASSWORD=Pw-1"]
    assert kept_speaker_lines(tmp_path / "missing") == []


def test_the_public_push_key_is_written_once_made():
    from botocore.exceptions import ClientError

    from crewops.web_env import push_key_line

    class Ssm:
        def __init__(self, value):
            self.value = value

        def get_parameter(self, Name):
            assert Name == "/crewops-dev/push/vapid-public"
            if self.value is None:
                raise ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter")
            return {"Parameter": {"Value": self.value}}

    assert push_key_line(Ssm("BAbc_d-1"), "/crewops-dev/push") == [
        "CREWOPS_PUSH_PUBLIC_KEY=BAbc_d-1"
    ]
    assert push_key_line(Ssm(None), "/crewops-dev/push") == []
    assert push_key_line(Ssm("x\nAWS_PROFILE=evil"), "/crewops-dev/push") == []
