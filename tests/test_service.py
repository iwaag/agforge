"""Deterministic-shell tests.

Agent behavior itself is NOT unit-tested — it is observed live and
recorded in episode reports. These tests pin only the shell around the
one agentic run: charter composition, how the agent's answer is picked
up, budget/timeout handling, and the HTTP surface, all through the
test-only `fake` profile (tests/fake_agent.py).

Since unshackle_agent turn1 there is nothing here that asserts the agent
said the right thing — the shell has no opinion about that any more.
"""

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from agag.agent_config import ResolvedAgent

from agforge import agent_run, request_service

TESTS_DIR = Path(__file__).resolve().parent
FAKE_AGENT = TESTS_DIR / "fake_agent.py"


@pytest.fixture
def agent(monkeypatch, tmp_path):
    config = tmp_path / "agents.toml"
    config.write_text(
        '''schema = "ag.agent-config.v1"
[models."ollama/test-model"]
[profiles.stub]
harness = "fake"
model = "ollama/test-model"
[roles.generator]
profile = "stub"
requires = []
'''
    )
    overlay = tmp_path / "agents.local.toml"
    overlay.write_text(
        f'''schema = "ag.agent-config.v1"
[local.harness.fake]
command = "{FAKE_AGENT}"
'''
    )
    monkeypatch.setattr(agent_run, "AGENTS_CONFIG", config)
    monkeypatch.setattr(agent_run, "AGENTS_LOCAL_CONFIG", overlay)
    monkeypatch.setenv("AGFORGE_PROBLEMS_DIR", str(tmp_path / "problems"))
    monkeypatch.setenv("AGFORGE_TRANSCRIPTS_DIR", str(tmp_path / "transcripts"))
    monkeypatch.setenv("AGFORGE_JOBS_DIR", str(tmp_path / "jobs"))
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

        def writes_result_wherever_told(self, body) -> None:
            """The agent answers at the path the charter named — id unknown here."""
            monkeypatch.setenv("FAKE_AGENT_RESULT", json.dumps(body))

        def writes_result(self, request_id: str, body) -> Path:
            """Stand in for the agent writing its own answer to disk."""
            path = agent_run.result_path(request_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body), encoding="utf-8")
            return path

        def capture_charter(self) -> Path:
            path = tmp_path / "charter.md"
            monkeypatch.setenv("FAKE_AGENT_CHARTER_OUT", str(path))
            return path

    return Agent()


# --- charter composition -------------------------------------------------

def test_charter_tells_the_agent_where_things_are(agent):
    charter = agent_run.compose_charter("a red dragon, 512x512", "abcdef0123456789")
    assert "a red dragon, 512x512" in charter          # desire, verbatim
    assert "abcdef0123456789" in charter               # request id
    assert "scripts/generate.sh" in charter            # the generation tool
    assert "acestudio-cli" in charter                  # the vocal-synthesis tool
    assert "service/transform.py" in charter           # the post-processing tool
    assert "service/GUIDE.md" in charter               # the capability card
    assert str(agent_run.result_path("abcdef0123456789")) in charter
    assert str(agent_run.problems_dir()) in charter    # the inbox, not a path rule
    assert str(agent_run.DEFAULT_BUDGET_SECONDS) in charter
    assert "{{" not in charter                         # no unfilled placeholder


def test_charter_honors_directory_overrides(agent, monkeypatch, tmp_path):
    monkeypatch.setenv("AGFORGE_PROBLEMS_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("AGFORGE_JOBS_DIR", str(tmp_path / "otherjobs"))
    charter = agent_run.compose_charter("x", "deadbeef00000000")
    assert str(tmp_path / "elsewhere") in charter
    assert str(tmp_path / "otherjobs") in charter


def test_charter_reaches_the_agent_verbatim(agent):
    captured = agent.capture_charter()
    agent_run.run_request("a very specific desire", request_id="cafe0000cafe0000")
    body = captured.read_text()
    assert "a very specific desire" in body
    assert "cafe0000" in body


# --- the agent's own answer, served as written -----------------------------

def test_result_file_is_served_unvalidated(agent):
    agent.writes_result(
        "aaaa0000aaaa0000",
        {
            "status": "done",
            "artifacts": [{"kind": "image", "url": "http://x.example/a.png"}],
            "reply": "here you go",
            "whatever_the_agent_wants": {"nested": [1, 2, 3]},
        },
    )
    agent.output("some closing prose, no marker at all")
    job, meta = agent_run.run_request("d", request_id="aaaa0000aaaa0000")
    assert job["status"] == "done"
    assert job["reply"] == "here you go"
    assert job["whatever_the_agent_wants"] == {"nested": [1, 2, 3]}
    assert meta["outcome_from"] == "result_file"


def test_the_file_is_the_answer_even_when_the_prose_disagrees(agent):
    agent.writes_result("bbbb0000bbbb0000", {"status": "failed", "detail": "I said so"})
    agent.output("actually here is a link: http://x.example/stale.png")
    job, _ = agent_run.run_request("d", request_id="bbbb0000bbbb0000")
    assert job == {"status": "failed", "detail": "I said so"}


def test_status_less_result_is_still_served(agent):
    """The runner fills only what it alone knows: the run is over.

    Nothing else is added — turn3 deleted the `artifacts` default, which
    no agent had used in 13 live runs.
    """
    agent.writes_result("cccc0000cccc0000", {"reply": "just words"})
    agent.output("prose")
    job, _ = agent_run.run_request("d", request_id="cccc0000cccc0000")
    assert job == {"reply": "just words", "status": "ended"}


def test_result_file_survives_a_dead_process(agent):
    """Answering and then dying still counts as answering."""
    agent.writes_result(
        "dddd0000dddd0000",
        {"status": "done", "artifacts": [{"kind": "image", "url": "http://x.example/a.png"}]},
    )
    agent.exit_code(3)
    job, meta = agent_run.run_request("d", request_id="dddd0000dddd0000")
    assert job["status"] == "done"
    assert meta["outcome_from"] == "result_file"
    assert "agent exited 3" in meta["infra_error"]


def test_unparseable_result_file_leaves_nothing_for_the_caller(agent):
    path = agent_run.result_path("eeee0000eeee0000")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")
    agent.output("I wrote something odd.")
    job, meta = agent_run.run_request("d", request_id="eeee0000eeee0000")
    assert job["status"] == "ended"
    assert meta["outcome_from"] == "nothing"
    assert "I wrote something odd." in job["detail"]


def test_the_agent_names_its_own_keys(agent):
    """Six different key names appeared live in turn2; none is special."""
    agent.writes_result("aced0000aced0000", {"outcome": "complete", "download": "http://x/y.zip"})
    agent.output("prose")
    job, _ = agent_run.run_request("d", request_id="aced0000aced0000")
    assert job == {"outcome": "complete", "download": "http://x/y.zip", "status": "ended"}


# --- nothing left for the caller -------------------------------------------

def test_no_answer_reports_the_fact_and_keeps_the_words(agent):
    agent.output("I am terribly confused about all of this.")
    job, meta = agent_run.run_request("d", request_id="ffff0000ffff0000")
    assert job["status"] == "ended"
    assert "left nothing for the caller" in job["detail"]
    assert "terribly confused" in job["detail"]     # the agent's own words survive
    assert meta["outcome_from"] == "nothing"


def test_prose_alone_is_not_an_answer(agent):
    agent.output("the link is somewhere in my message above, probably")
    job, _ = agent_run.run_request("d")
    assert job["status"] == "ended"


# --- the `fake` harness passthrough ------------------------------------------

def test_stub_output_reaches_the_caller_unparsed(agent):
    """`fake` has no event stream to parse: whatever the stub prints is the
    answer, and it reports no turn or cost statistics of its own."""
    agent.writes_result_wherever_told({"status": "done", "url": "http://x.example/a.png"})
    agent.output("done, see the file")
    job, meta = agent_run.run_request("d")
    assert job == {"status": "done", "url": "http://x.example/a.png"}
    assert "cost_usd" not in meta
    assert "num_turns" not in meta


def test_resolved_secret_environment_reaches_subprocess(agent, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_AGENT_ECHO_ENV", "ANTHROPIC_API_KEY")
    resolved = ResolvedAgent(
        role="generator",
        profile="stub",
        harness="fake",
        provider="anthropic",
        model="anthropic/test-model",
        model_options={},
        command=str(FAKE_AGENT),
        provider_base_url=None,
        environment={"ANTHROPIC_API_KEY": "injected-test-value"},
    )
    output, meta = agent_run.run_agent("charter", 5, resolved)
    assert output.strip() == "injected-test-value"
    assert meta["outcome"] == "done"


# --- transcript capture ----------------------------------------------------

def test_transcript_written_and_pointed_at(agent):
    agent.output("here is what I did, at length")
    _, meta = agent_run.run_request("d", request_id="feed0000feed0000")
    transcript = agent.transcript("feed0000feed0000")
    assert meta["transcript"] == str(transcript)
    assert "here is what I did" in transcript.read_text()


def test_normalized_run_record_uses_canonical_identity(agent):
    agent.output("completed")
    _, meta = agent_run.run_request("d", request_id="face0000face0000")
    record = json.loads(Path(meta["run_record"]).read_text())
    assert record == {
        "schema": "ag.agent-run.v1",
        "request_id": "face0000face0000",
        "role": "generator",
        "profile": "stub",
        "harness": "fake",
        "provider": "ollama",
        "model": "ollama/test-model",
        "duration_ms": record["duration_ms"],
        "transcript": str(agent.transcript("face0000face0000")),
        "outcome": "done",
    }
    assert "backend" not in record


def test_transcript_survives_infra_failure(agent):
    agent.output("half an answer before dying")
    agent.exit_code(3)
    job, meta = agent_run.run_request("d", request_id="dead0000dead0000")
    assert job["status"] == "failed"
    assert meta["transcript"] == str(agent.transcript("dead0000dead0000"))
    assert "half an answer" in agent.transcript("dead0000dead0000").read_text()


# --- infra failures and budget ---------------------------------------------

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
    assert "agent exited 1" in job["detail"]
    assert "stderr tail: ollama: connection refused" in job["detail"]


def test_empty_output_keeps_stderr_tail(agent):
    agent.output("")
    agent.stderr("panic: something harness-side")
    job, _ = agent_run.run_request("d")
    assert "agent produced no output" in job["detail"]
    assert "stderr tail: panic: something harness-side" in job["detail"]


def test_budget_timeout_fails_loudly(agent):
    agent.output("too late to matter")
    agent.sleep(5)
    started = time.monotonic()
    job, _ = agent_run.run_request("d", budget_seconds=1)
    assert time.monotonic() - started < 4
    assert job["status"] == "failed"
    assert "timed out" in job["detail"]


# --- run_job mapping -------------------------------------------------------

def test_run_job_publishes_what_the_agent_said(agent):
    agent.writes_result("job1", {"status": "done", "image_url": "http://x.example/a.png"})
    agent.output("prose")
    request_service.run_job("job1", "a dragon")
    assert request_service.jobs["job1"] == {
        "status": "done",
        "image_url": "http://x.example/a.png",
    }


# --- HTTP surface -----------------------------------------------------------

@pytest.fixture
def server(agent):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), request_service.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
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
    agent.writes_result_wherever_told({"status": "done", "url": "http://x.example/a.png"})
    agent.output("answered in the file")
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
    assert job == {"status": "done", "url": "http://x.example/a.png"}


def test_every_desire_reaches_the_agent(agent, server):
    """No classifier short-circuits a question any more; the agent answers."""
    agent.writes_result_wherever_told({"status": "answered", "reply": "it costs nothing"})
    agent.output("I read the card and answered in words")
    status, body = http("POST", f"{server}/api/requests", {"desire": "what does it cost?"})
    assert status == 202
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        _, job = http("GET", f"{server}/api/requests/{body['request_id']}")
        if job["status"] != "working":
            break
        time.sleep(0.05)
    assert job == {"status": "answered", "reply": "it costs nothing"}


def test_http_bad_request_and_unknown_id(agent, server):
    status, _ = http("POST", f"{server}/api/requests", {"nope": 1})
    assert status == 400
    status, _ = http("GET", f"{server}/api/requests/doesnotexist")
    assert status == 404


def test_the_card_is_served_raw_and_re_read_per_request(agent, server, monkeypatch, tmp_path):
    card = tmp_path / "GUIDE.md"
    card.write_text("agforge makes one still image per request.\n")
    monkeypatch.setattr(request_service, "GUIDE_PATH", card)
    with urllib.request.urlopen(urllib.request.Request(f"{server}/guide")) as response:
        assert response.read().decode() == "agforge makes one still image per request.\n"
    card.write_text("and now it also makes music.\n")
    with urllib.request.urlopen(urllib.request.Request(f"{server}/api/guide")) as response:
        assert response.read().decode() == "and now it also makes music.\n"


def test_a_missing_card_does_not_break_the_service(monkeypatch, tmp_path):
    monkeypatch.setattr(request_service, "GUIDE_PATH", tmp_path / "absent.md")
    assert "No capability card" in request_service.read_guide()
