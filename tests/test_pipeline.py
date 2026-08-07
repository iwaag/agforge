import json
import sys
from pathlib import Path

import pytest
from PIL import Image

import request_service

TESTS_DIR = Path(__file__).resolve().parent
FAKE_LLM = f"{sys.executable} {TESTS_DIR / 'fake_llm.py'}"
FAKE_GENERATE = f"{sys.executable} {TESTS_DIR / 'fake_generate.py'}"


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("AGFORGE_INTERPRET_CMD", FAKE_LLM)
    monkeypatch.setenv("FAKE_LLM_STATE", str(tmp_path / "llm-calls"))
    monkeypatch.setenv("AGFORGE_GENERATE_CMD", FAKE_GENERATE)
    monkeypatch.setenv("FAKE_GEN_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("FAKE_GEN_STATE", str(tmp_path / "gen-calls"))
    monkeypatch.setenv("AGFORGE_PROBLEMS_DIR", str(tmp_path / "problems"))
    request_service.jobs.clear()

    class Pipeline:
        def llm(self, *results: str) -> None:
            monkeypatch.setenv("FAKE_LLM_RESULTS", json.dumps(list(results)))

        def gen_sizes(self, *sizes: str) -> None:
            monkeypatch.setenv("FAKE_GEN_SIZES", json.dumps(list(sizes)))

        def gen_calls(self) -> int:
            state = tmp_path / "gen-calls"
            return int(state.read_text()) if state.exists() else 0

        def run(self, desire: str = "anything") -> dict:
            request_service.run_job("job1", desire)
            return request_service.jobs["job1"]

        def problem_reports(self) -> list[Path]:
            root = tmp_path / "problems"
            return sorted(root.glob("*/problem.md")) if root.exists() else []

        tmp_path = None

    pipeline = Pipeline()
    pipeline.tmp_path = tmp_path
    return pipeline


def test_happy_path_exact_size(pipeline):
    pipeline.llm('{"prompt": "a dragon", "width": 512, "height": 512, "refuse": false}')
    job = pipeline.run("a dragon, 512x512")
    assert job["status"] == "done"
    assert job["artifacts"][0]["kind"] == "image"
    assert job["artifacts"][0]["url"].startswith("http://fake.example/generated/")
    assert pipeline.gen_calls() == 1


def test_size_silent_desire_uses_defaults(pipeline):
    pipeline.llm('{"prompt": "a cabin", "width": null, "height": null, "refuse": false}')
    job = pipeline.run("a cabin")
    assert job["status"] == "done"
    assert pipeline.gen_calls() == 1


def test_wrong_size_retries_then_succeeds(pipeline):
    pipeline.llm('{"prompt": "a dragon", "width": 512, "height": 512, "refuse": false}')
    pipeline.gen_sizes("300x300", "512x512")
    job = pipeline.run()
    assert job["status"] == "done"
    assert pipeline.gen_calls() == 2
    assert job["artifacts"][0]["url"].startswith("http://fake.example/generated/")


def test_rounded_size_resizes_to_exact_request(pipeline, monkeypatch):
    pipeline.llm('{"prompt": "an astronaut", "width": 513, "height": 300, "refuse": false}')
    uploaded = {}

    def fake_upload(env, local_path, ttl):
        uploaded["path"] = Path(local_path)
        return f"http://fake.example/resized/{Path(local_path).name}"

    monkeypatch.setattr(request_service.generate_module, "load_env", lambda: {})
    monkeypatch.setattr(request_service.generate_module, "upload_and_presign", fake_upload)
    job = pipeline.run("an astronaut, 513 by 300ish")
    assert job["status"] == "done"
    assert job["artifacts"][0]["url"].startswith("http://fake.example/resized/")
    with Image.open(uploaded["path"]) as image:
        assert image.size == (513, 300)
    # generator was asked for the rounded size and obeyed — no pointless retry
    assert pipeline.gen_calls() == 1


def test_format_mismatch_converts(pipeline, monkeypatch):
    # fake_generate always writes PNG; a jpeg desire forces the convert path
    pipeline.llm(
        '{"prompt": "a dragon", "width": 512, "height": 512, "format": "jpeg", "refuse": false}'
    )
    uploaded = {}

    def fake_upload(env, local_path, ttl):
        uploaded["path"] = Path(local_path)
        return f"http://fake.example/converted/{Path(local_path).name}"

    monkeypatch.setattr(request_service.generate_module, "load_env", lambda: {})
    monkeypatch.setattr(request_service.generate_module, "upload_and_presign", fake_upload)
    job = pipeline.run("a dragon, 512x512, as jpeg")
    assert job["status"] == "done"
    assert job["artifacts"][0]["url"].startswith("http://fake.example/converted/")
    with Image.open(uploaded["path"]) as image:
        assert image.format == "JPEG"
        assert image.size == (512, 512)
    assert pipeline.gen_calls() == 1
    assert pipeline.problem_reports() == []


def test_format_match_delivers_as_generated(pipeline):
    pipeline.llm(
        '{"prompt": "a dragon", "width": null, "height": null, "format": "png", "refuse": false}'
    )
    job = pipeline.run("a dragon, png")
    assert job["status"] == "done"
    assert job["artifacts"][0]["url"].startswith("http://fake.example/generated/")


def test_resize_and_convert_together(pipeline, monkeypatch):
    pipeline.llm(
        '{"prompt": "a mural", "width": 513, "height": 300, "format": "jpeg", "refuse": false}'
    )
    uploaded = {}

    def fake_upload(env, local_path, ttl):
        uploaded["path"] = Path(local_path)
        return f"http://fake.example/transformed/{Path(local_path).name}"

    monkeypatch.setattr(request_service.generate_module, "load_env", lambda: {})
    monkeypatch.setattr(request_service.generate_module, "upload_and_presign", fake_upload)
    job = pipeline.run("a mural, 513 by 300, jpeg please")
    assert job["status"] == "done"
    with Image.open(uploaded["path"]) as image:
        assert image.format == "JPEG"
        assert image.size == (513, 300)


def test_wrong_aspect_fails_unsatisfied(pipeline):
    pipeline.llm('{"prompt": "a dragon", "width": 512, "height": 512, "refuse": false}')
    pipeline.gen_sizes("640x320", "640x320")
    job = pipeline.run()
    assert job["status"] == "failed"
    assert job["detail"].startswith("unsatisfied:")
    assert pipeline.gen_calls() == 2


def test_refusal_short_circuits_before_generate(pipeline, monkeypatch):
    marker = pipeline.tmp_path / "gen-was-called"
    monkeypatch.setenv("FAKE_GEN_MARKER", str(marker))
    pipeline.llm('{"refuse": true, "reason": "agforge cannot generate music."}')
    job = pipeline.run("a lofi track")
    assert job["status"] == "failed"
    assert job["detail"] == "refused: agforge cannot generate music."
    assert not marker.exists()


def test_out_of_bounds_size_is_refused(pipeline, monkeypatch):
    marker = pipeline.tmp_path / "gen-was-called"
    monkeypatch.setenv("FAKE_GEN_MARKER", str(marker))
    pipeline.llm('{"prompt": "a mural", "width": 4096, "height": 4096, "refuse": false}')
    job = pipeline.run("a huge mural, 4096x4096")
    assert job["status"] == "failed"
    assert job["detail"].startswith("refused:")
    assert not marker.exists()


def test_interpreter_error_fails_job(pipeline):
    pipeline.llm("not json", "still not json")
    job = pipeline.run()
    assert job["status"] == "failed"
    assert job["detail"].startswith("interpreter error:")
    assert pipeline.gen_calls() == 0


def test_refusal_writes_problem_report(pipeline):
    pipeline.llm('{"refuse": true, "reason": "agforge cannot generate music."}')
    pipeline.run("a lofi track")
    reports = pipeline.problem_reports()
    assert len(reports) == 1
    body = reports[0].read_text()
    assert "a lofi track" in body
    assert "refused: agforge cannot generate music." in body
    assert "request_id: job1" in body


def test_unsatisfied_writes_problem_report(pipeline):
    pipeline.llm('{"prompt": "a dragon", "width": 512, "height": 512, "refuse": false}')
    pipeline.gen_sizes("640x320", "640x320")
    pipeline.run("a dragon, 512x512")
    reports = pipeline.problem_reports()
    assert len(reports) == 1
    assert "unsatisfied:" in reports[0].read_text()


def test_success_and_infra_errors_write_no_problem_report(pipeline):
    pipeline.llm('{"prompt": "a dragon", "width": 512, "height": 512, "refuse": false}')
    job = pipeline.run("a dragon, 512x512")
    assert job["status"] == "done"
    assert pipeline.problem_reports() == []

    request_service.jobs.clear()
    pipeline.llm("not json", "still not json")
    job = pipeline.run()
    assert job["status"] == "failed"
    assert pipeline.problem_reports() == []
