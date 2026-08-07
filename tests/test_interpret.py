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
