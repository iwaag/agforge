"""Deterministic-shell tests (agentify ex2).

Agent behavior itself is NOT unit-tested — it is observed live and
recorded in episode reports. These tests pin the deterministic shell
around the one agentic run: charter composition, lenient outcome
parsing, budget/timeout handling, and the HTTP contract, all through the
AGFORGE_AGENT_CMD stub (tests/fake_agent.py).
"""

import json
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_run
import request_service

TESTS_DIR = Path(__file__).resolve().parent
FAKE_AGENT = f"{sys.executable} {TESTS_DIR / 'fake_agent.py'}"


@pytest.fixture
def agent(monkeypatch, tmp_path):
    monkeypatch.setenv("AGFORGE_AGENT_CMD", FAKE_AGENT)
    monkeypatch.setenv("AGFORGE_PROBLEMS_DIR", str(tmp_path / "problems"))
    request_service.jobs.clear()

    class Agent:
        def output(self, text: str) -> None:
            monkeypatch.setenv("FAKE_AGENT_OUTPUT", text)

        def exit_code(self, code: int) -> None:
            monkeypatch.setenv("FAKE_AGENT_EXIT", str(code))

        def sleep(self, seconds: float) -> None:
            monkeypatch.setenv("FAKE_AGENT_SLEEP", str(seconds))

        def capture_charter(self) -> Path:
            path = tmp_path / "charter.md"
            monkeypatch.setenv("FAKE_AGENT_CHARTER_OUT", str(path))
            return path

    return Agent()


# --- charter composition -------------------------------------------------

def test_charter_contains_the_need_to_know(agent):
    charter = agent_run.compose_charter("a red dragon, 512x512", "abcdef0123456789")
    assert "a red dragon, 512x512" in charter          # desire, verbatim
    assert "abcdef0123456789" in charter               # request id
    assert "scripts/generate.sh" in charter            # the generation tool
    assert "service/transform.py" in charter           # the post-processing tool
    assert "python -c" not in charter                  # fragile snippet retired (ex3)
    assert "RESULT_URL:" in charter and "RESULT_FAILED:" in charter
    assert "nctl-outbox" in charter                    # bucket rule
    assert str(agent_run.DEFAULT_BUDGET_SECONDS) in charter
    # the problem-report path rule: problems dir + request_id[:8]
    assert "abcdef01" in charter
    assert "problem.md" in charter
    assert "{{" not in charter                         # no unfilled placeholder


def test_charter_problem_path_honors_problems_dir_override(agent, monkeypatch, tmp_path):
    monkeypatch.setenv("AGFORGE_PROBLEMS_DIR", str(tmp_path / "elsewhere"))
    charter = agent_run.compose_charter("x", "deadbeef00000000")
    assert str(tmp_path / "elsewhere") in charter


def test_charter_reaches_the_agent_verbatim(agent):
    captured = agent.capture_charter()
    agent.output("RESULT_FAILED: nope")
    agent_run.run_request("a very specific desire", request_id="cafe0000cafe0000")
    body = captured.read_text()
    assert "a very specific desire" in body
    assert "cafe0000" in body


# --- lenient outcome parsing ---------------------------------------------

def test_url_outcome_tolerates_prose(agent):
    agent.output("I generated the image.\n\nRESULT_URL: http://x.example/a.png?sig=1\nthanks!")
    job, meta = agent_run.run_request("d")
    assert job == {
        "status": "done",
        "artifacts": [{"kind": "image", "url": "http://x.example/a.png?sig=1"}],
    }
    assert meta["backend"] == "override"


def test_failed_outcome_tolerates_markdown_decoration(agent):
    agent.output("**RESULT_FAILED:** cannot generate music")
    job, _ = agent_run.run_request("d")
    assert job == {"status": "failed", "detail": "cannot generate music"}


def test_last_marker_wins(agent):
    agent.output("RESULT_URL: http://x.example/draft.png\nRESULT_FAILED: upload broke")
    job, _ = agent_run.run_request("d")
    assert job["status"] == "failed"
    assert job["detail"] == "upload broke"


def test_no_marker_fails_with_output_tail(agent):
    agent.output("I am terribly confused about all of this.")
    job, _ = agent_run.run_request("d")
    assert job["status"] == "failed"
    assert "without a RESULT marker" in job["detail"]
    assert "terribly confused" in job["detail"]


def test_non_http_url_candidate_is_not_a_result(agent):
    agent.output("RESULT_URL: (I will paste it here later)")
    job, _ = agent_run.run_request("d")
    assert job["status"] == "failed"
    assert "without a RESULT marker" in job["detail"]


# --- infra failures and budget --------------------------------------------

def test_agent_nonzero_exit_is_an_infra_failure(agent):
    agent.output("half an answer")
    agent.exit_code(3)
    job, _ = agent_run.run_request("d")
    assert job["status"] == "failed"
    assert "agent exited 3" in job["detail"]


def test_budget_timeout_fails_loudly(agent):
    agent.output("RESULT_URL: http://x.example/too-late.png")
    agent.sleep(5)
    started = time.monotonic()
    job, _ = agent_run.run_request("d", budget_seconds=1)
    assert time.monotonic() - started < 4
    assert job["status"] == "failed"
    assert "timed out" in job["detail"]


# --- run_job mapping -------------------------------------------------------

def test_run_job_finishes_done(agent):
    agent.output("RESULT_URL: http://x.example/a.png")
    request_service.run_job("job1", "a dragon")
    assert request_service.jobs["job1"] == {
        "status": "done",
        "artifacts": [{"kind": "image", "url": "http://x.example/a.png"}],
    }


def test_run_job_finishes_failed(agent):
    agent.output("RESULT_FAILED: cannot do that")
    request_service.run_job("job1", "a song")
    assert request_service.jobs["job1"] == {"status": "failed", "detail": "cannot do that"}


# --- HTTP contract ----------------------------------------------------------

@pytest.fixture
def server(agent):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), request_service.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def http(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_http_contract_end_to_end(agent, server):
    agent.output("RESULT_URL: http://x.example/a.png")
    status, body = http("GET", f"{server}/healthz")
    assert (status, body) == (200, {"ok": True})

    status, body = http("POST", f"{server}/api/requests", {"desire": "a dragon"})
    assert status == 202
    request_id = body["request_id"]

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status, job = http("GET", f"{server}/api/requests/{request_id}")
        assert status == 200
        if job["status"] != "working":
            break
        time.sleep(0.05)
    assert job["status"] == "done"
    assert job["artifacts"] == [{"kind": "image", "url": "http://x.example/a.png"}]


def test_http_bad_request_and_unknown_id(agent, server):
    status, body = http("POST", f"{server}/api/requests", {"nope": 1})
    assert status == 400
    status, body = http("GET", f"{server}/api/requests/doesnotexist")
    assert status == 404
