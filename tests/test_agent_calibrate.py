"""Setting the router's similarity floor from measured matches (fake embeddings here)."""

from dataclasses import dataclass

from agent_fixtures import examples, paraphrases, vocab

from crewops.agent import calibrate as calibrate_command
from crewops.agent.calibrate import calibrate
from crewops.agent.router import Router


@dataclass
class Hit:
    key: str
    semantic: float


def test_floor_sits_above_every_off_topic_and_wrong_match():
    router = Router(vocab(), examples())
    data = paraphrases()
    sources = {p["prompt"]: p["of"] for p in data["paraphrases"]}

    def search(text):
        if text in sources:
            if text.startswith("Which crew are standing by"):
                return [Hit("Q31", 0.52)]  # a wrong match: must be refused
            return [Hit(sources[text], 0.70)]
        return [Hit("Q01", 0.41)]  # off-topic

    report = calibrate(data, router.example_intents, search)
    assert report.floor == 0.53
    passed, total = report.cleared(report.floor)
    assert passed == total == len(data["paraphrases"]) - 1
    assert "Recommended floor: 0.53" in "\n".join(report.lines())


def test_every_paraphrase_names_a_real_example():
    ids = set(examples())
    data = paraphrases()
    assert {p["of"] for p in data["paraphrases"]} == ids
    assert len(data["off_topic"]) >= 10
    prompts = {q for q in examples().values()}
    assert not prompts & {p["prompt"] for p in data["paraphrases"]}


def test_calibrate_needs_a_named_profile(monkeypatch, capsys):
    monkeypatch.setattr("crewops.config.load_env_file", lambda: {})
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    assert calibrate_command.main([]) == 1
    assert "AWS_PROFILE" in capsys.readouterr().err
