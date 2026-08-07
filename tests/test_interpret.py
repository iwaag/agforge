import json
import sys
from pathlib import Path

import pytest

import interpret

TESTS_DIR = Path(__file__).resolve().parent
FAKE_LLM = f"{sys.executable} {TESTS_DIR / 'fake_llm.py'}"


@pytest.fixture
def fake_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("AGFORGE_INTERPRET_CMD", FAKE_LLM)
    monkeypatch.setenv("FAKE_LLM_STATE", str(tmp_path / "llm-calls"))

    def set_results(*results: str) -> None:
        monkeypatch.setenv("FAKE_LLM_RESULTS", json.dumps(list(results)))

    return set_results


def test_field_extraction(fake_llm):
    fake_llm('{"prompt": "a red dragon", "width": 512, "height": 512, "refuse": false}')
    interpretation, meta = interpret.interpret("a red dragon, 512x512")
    assert interpretation == {
        "prompt": "a red dragon",
        "width": 512,
        "height": 512,
        "refuse": False,
    }
    assert meta["attempts"] == 1
    assert meta["total_cost_usd"] == 0.01


def test_null_passthrough(fake_llm):
    fake_llm('{"prompt": "a cozy cabin", "width": null, "height": null, "refuse": false}')
    interpretation, _ = interpret.interpret("a cozy cabin")
    assert interpretation["width"] is None
    assert interpretation["height"] is None


def test_refusal(fake_llm):
    fake_llm('{"refuse": true, "reason": "agforge cannot generate music."}')
    interpretation, _ = interpret.interpret("a lofi track")
    assert interpretation == {"refuse": True, "reason": "agforge cannot generate music."}


def test_refusal_without_reason_is_malformed(fake_llm):
    fake_llm('{"refuse": true}')
    with pytest.raises(interpret.InterpretError):
        interpret.interpret("a lofi track")


def test_malformed_json_retries_once_then_succeeds(fake_llm):
    fake_llm(
        "sorry, here is your JSON:",
        '{"prompt": "ok", "width": null, "height": null, "refuse": false}',
    )
    interpretation, meta = interpret.interpret("anything")
    assert interpretation["prompt"] == "ok"
    assert meta["attempts"] == 2


def test_malformed_json_twice_fails(fake_llm):
    fake_llm("not json", "still not json")
    with pytest.raises(interpret.InterpretError):
        interpret.interpret("anything")


def test_non_integer_dimension_is_malformed(fake_llm):
    fake_llm('{"prompt": "x", "width": "512", "height": null, "refuse": false}')
    with pytest.raises(interpret.InterpretError):
        interpret.interpret("anything")


@pytest.mark.parametrize(
    ("value", "expected", "changed"),
    [
        (None, None, False),
        (512, 512, False),
        (513, 512, True),
        (300, 320, True),
        (64, 64, False),
        (2048, 2048, False),
    ],
)
def test_validate_dimension(value, expected, changed):
    assert interpret.validate_dimension(value) == (expected, changed)


@pytest.mark.parametrize("value", [63, 0, -512, 2049])
def test_validate_dimension_out_of_bounds(value):
    with pytest.raises(interpret.InterpretError):
        interpret.validate_dimension(value)


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Detach interpret from the real `.local/.env` and process env."""
    monkeypatch.setattr(interpret, "AGFORGE_ROOT", tmp_path)
    for name in (
        "AGFORGE_INTERPRET_CMD",
        "AGFORGE_INTERPRET_BACKEND",
        "AGFORGE_OLLAMA_URL",
        "AGFORGE_OLLAMA_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def fake_ollama(monkeypatch):
    """Stub urlopen; returns a dict whose 'response' key drives the answer."""
    state = {"response": "", "requests": []}

    class FakeHTTPResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(
                {"response": state["response"], "total_duration": 1_500_000_000}
            ).encode()

    def fake_urlopen(request, timeout=None):
        state["requests"].append(json.loads(request.data))
        return FakeHTTPResponse()

    monkeypatch.setattr(interpret.urllib.request, "urlopen", fake_urlopen)
    return state


def test_backend_defaults_to_claude(isolated_env):
    assert interpret.interpret_backend() == "claude"


def test_backend_from_env_file(isolated_env):
    (isolated_env / ".local").mkdir()
    (isolated_env / ".local" / ".env").write_text("AGFORGE_INTERPRET_BACKEND=ollama\n")
    assert interpret.interpret_backend() == "ollama"


def test_unknown_backend_is_an_error(isolated_env, monkeypatch):
    monkeypatch.setenv("AGFORGE_INTERPRET_BACKEND", "gpt")
    with pytest.raises(interpret.InterpretError):
        interpret.interpret_backend()


def test_ollama_unconfigured_is_an_error(isolated_env, monkeypatch):
    monkeypatch.setenv("AGFORGE_INTERPRET_BACKEND", "ollama")
    with pytest.raises(interpret.InterpretError, match="not configured"):
        interpret.run_llm("prompt")


def test_ollama_interpretation(isolated_env, monkeypatch, fake_ollama):
    monkeypatch.setenv("AGFORGE_INTERPRET_BACKEND", "ollama")
    monkeypatch.setenv("AGFORGE_OLLAMA_URL", "http://ollama.test:11434")
    monkeypatch.setenv("AGFORGE_OLLAMA_MODEL", "test-model")
    fake_ollama["response"] = '{"prompt": "a red dragon", "width": 512, "height": 512, "refuse": false}'
    interpretation, meta = interpret.interpret("a red dragon, 512x512")
    assert interpretation["prompt"] == "a red dragon"
    assert meta["backend"] == "ollama"
    assert meta["duration_ms"] == 1500
    sent = fake_ollama["requests"][0]
    assert sent["model"] == "test-model"
    assert sent["format"] == "json"
    assert sent["options"] == {"temperature": 0}


def test_interpret_cmd_overrides_backend(isolated_env, monkeypatch, fake_llm):
    monkeypatch.setenv("AGFORGE_INTERPRET_BACKEND", "ollama")
    fake_llm('{"prompt": "ok", "width": null, "height": null, "refuse": false}')
    interpretation, meta = interpret.interpret("anything")
    assert interpretation["prompt"] == "ok"
    assert meta["backend"] == "override"
