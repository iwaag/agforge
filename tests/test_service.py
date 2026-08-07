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
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
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
    monkeypatch.setenv("AGFORGE_TRANSCRIPTS_DIR", str(tmp_path / "transcripts"))
    # URL verification is exercised separately (its own test section below);
    # everywhere else the fake agent's example URLs must not hit the network.
    monkeypatch.setattr(
        agent_run,
        "verify_result_url",
        lambda url: {"ok": True, "status": 200, "content_type": "image/png", "size_bytes": 1},
    )
    request_service.jobs.clear()

    class Agent:
        def output(self, text: str) -> None:
            monkeypatch.setenv("FAKE_AGENT_OUTPUT", text)

        def exit_code(self, code: int) -> None:
            monkeypatch.setenv("FAKE_AGENT_EXIT", str(code))

        def sleep(self, seconds: float) -> None:
            monkeypatch.setenv("FAKE_AGENT_SLEEP", str(seconds))

        def stderr(self, text: str) -> None:
            monkeypatch.setenv("FAKE_AGENT_STDERR", text)

        def transcript(self, request_id: str) -> Path:
            return tmp_path / "transcripts" / f"{request_id}.agent.jsonl"

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


# --- opencode event-stream extraction (ex3) ---------------------------------

def test_event_stream_yields_text_and_stats():
    raw = "\n".join([
        '{"type":"step_start","part":{}}',
        '{"type":"text","part":{"text":"Generating now."}}',
        '{"type":"tool","part":{"tool":"bash"}}',
        '{"type":"step_finish","part":{"cost":0.01}}',
        '{"type":"text","part":{"text":"RESULT_URL: http://x.example/a.png"}}',
        '{"type":"step_finish","part":{"cost":0.02}}',
    ])
    text, stats = agent_run.extract_event_text(raw)
    assert text == "Generating now.\nRESULT_URL: http://x.example/a.png"
    assert stats == {"num_turns": 2, "total_cost_usd": 0.03}


def test_plain_text_passes_through_unchanged():
    raw = "just prose\nRESULT_FAILED: nope"
    text, stats = agent_run.extract_event_text(raw)
    assert text == raw
    assert stats == {}


def test_event_stream_outcome_parses_end_to_end(agent):
    agent.output('{"type":"text","part":{"text":"RESULT_URL: http://x.example/a.png"}}')
    job, meta = agent_run.run_request("d")
    assert job["status"] == "done"
    assert meta["num_turns"] == 0


# --- transcript capture (ex3) -----------------------------------------------

def test_transcript_written_and_pointed_at(agent):
    agent.output("RESULT_URL: http://x.example/a.png")
    job, meta = agent_run.run_request("d", request_id="feed0000feed0000")
    transcript = agent.transcript("feed0000feed0000")
    assert meta["transcript"] == str(transcript)
    assert "RESULT_URL" in transcript.read_text()


def test_transcript_survives_infra_failure(agent):
    agent.output("half an answer before dying")
    agent.exit_code(3)
    job, meta = agent_run.run_request("d", request_id="dead0000dead0000")
    assert job["status"] == "failed"
    assert meta["transcript"] == str(agent.transcript("dead0000dead0000"))
    assert "half an answer" in agent.transcript("dead0000dead0000").read_text()


# --- infra failures and budget --------------------------------------------

def test_agent_nonzero_exit_is_an_infra_failure(agent):
    agent.output("half an answer")
    agent.exit_code(3)
    job, _ = agent_run.run_request("d")
    assert job["status"] == "failed"
    assert "agent exited 3" in job["detail"]


def test_nonzero_exit_keeps_stderr_tail(agent):
    agent.exit_code(1)
    agent.stderr("ollama: connection refused")
    job, _ = agent_run.run_request("d")
    assert job["status"] == "failed"
    assert "agent exited 1" in job["detail"]
    assert "stderr tail: ollama: connection refused" in job["detail"]


def test_empty_output_keeps_stderr_tail(agent):
    agent.output("")
    agent.stderr("panic: something harness-side")
    job, _ = agent_run.run_request("d")
    assert job["status"] == "failed"
    assert "agent produced no output" in job["detail"]
    assert "stderr tail: panic: something harness-side" in job["detail"]


def test_budget_timeout_fails_loudly(agent):
    agent.output("RESULT_URL: http://x.example/too-late.png")
    agent.sleep(5)
    started = time.monotonic()
    job, _ = agent_run.run_request("d", budget_seconds=1)
    assert time.monotonic() - started < 4
    assert job["status"] == "failed"
    assert "timed out" in job["detail"]


# --- runner-side URL verification (ex3) ------------------------------------

@pytest.fixture
def artifact_server(agent, monkeypatch):
    """Real HTTP server + real verify_result_url (fixture stub removed)."""
    monkeypatch.setattr(agent_run, "verify_result_url", REAL_VERIFY)

    class ArtifactHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/good.png":
                body = b"\x89PNG fake bytes"
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(403)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ArtifactHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


REAL_VERIFY = agent_run.verify_result_url


def test_verified_url_is_delivered_done_with_evidence(agent, artifact_server):
    agent.output(f"RESULT_URL: {artifact_server}/good.png")
    job, meta = agent_run.run_request("d")
    assert job["status"] == "done"
    assert meta["url_check"] == {
        "ok": True,
        "status": 200,
        "content_type": "image/png",
        "size_bytes": 15,
    }


def test_corrupted_url_fails_as_transcription_problem(agent, artifact_server):
    agent.output(f"RESULT_URL: {artifact_server}/corrupted-signature.png")
    job, meta = agent_run.run_request("d")
    assert job["status"] == "failed"
    assert "HTTP 403" in job["detail"]
    assert "mistranscribed" in job["detail"]
    assert meta["url_check"] == {"ok": False, "reason": "HTTP 403"}


def test_unreachable_url_fails_verification(agent, monkeypatch):
    monkeypatch.setattr(agent_run, "verify_result_url", REAL_VERIFY)
    agent.output("RESULT_URL: http://127.0.0.1:1/nope.png")
    job, _ = agent_run.run_request("d")
    assert job["status"] == "failed"
    assert "RESULT_URL failed verification" in job["detail"]


def test_failed_outcome_skips_verification(agent, monkeypatch):
    monkeypatch.setattr(agent_run, "verify_result_url", REAL_VERIFY)
    agent.output("RESULT_FAILED: cannot generate music")
    job, meta = agent_run.run_request("d")
    assert job == {"status": "failed", "detail": "cannot generate music"}
    assert "url_check" not in meta


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
