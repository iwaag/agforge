"""`agforge video submit` / `music submit` / `comfy fetch` without a ComfyUI.

The seam these verbs add is small — the existing generators already submit
and already download — so what is pinned is the part that is new: that
submit answers a `prompt_id` and does not wait, that fetch never polls, and
that the three ways a fetch can be pointless each say so in one line instead
of hanging or handing an agent a traceback.
"""

import argparse
import json

import pytest

from agforge import comfy_async


class FakeResponse:
    def __init__(self, payload, content=b""):
        self.payload, self.content = payload, content

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


def wire(monkeypatch, history, *, url="http://comfy.invalid"):
    monkeypatch.setattr(comfy_async, "comfyui_url", lambda: url)
    gets = []

    def get(target, **kwargs):
        gets.append(target)
        if "/history/" in target:
            return FakeResponse(history)
        return FakeResponse({}, content=b"PNGDATA")

    monkeypatch.setattr(comfy_async.requests, "get", get)
    return gets


def finished(prompt_id="p-1", filename="apple_00001_.png", status="success"):
    return {prompt_id: {
        "status": {"status_str": status, "completed": True},
        "outputs": {"9": {"images": [
            {"filename": filename, "subfolder": "", "type": "output"},
        ]}},
    }}


def message(error):
    return str(error.value)


def test_submit_answers_a_prompt_id_without_waiting_for_the_render(monkeypatch):
    """The whole point: a run can hand this id to the notifier and end."""
    monkeypatch.setattr(comfy_async, "comfyui_url", lambda: "http://comfy.invalid")
    monkeypatch.setattr(comfy_async.comfy_video, "load_workflow", lambda prompt: {"1": prompt})
    monkeypatch.setattr(comfy_async, "free_memory", lambda base: True)
    seen = {}
    monkeypatch.setattr(
        comfy_async, "submit",
        lambda base, workflow: seen.update(base=base, workflow=workflow) or "p-42",
    )
    assert comfy_async.submit_job("video", "an apple") == "p-42"
    assert seen["workflow"] == {"1": "an apple"}


def test_an_empty_prompt_is_refused_before_anything_is_queued(monkeypatch):
    monkeypatch.setattr(comfy_async, "submit_job", lambda *a: pytest.fail("queued"))
    with pytest.raises(SystemExit) as error:
        comfy_async.run_submit("video", argparse.Namespace(prompt="   "))
    assert "prompt is empty" in message(error)


def test_fetch_writes_every_output_under_comfyuis_own_filename(monkeypatch, tmp_path):
    """The names are ComfyUI's: a 125-frame graph numbers its frames, and
    renaming them here would destroy the only ordering there is."""
    wire(monkeypatch, finished())
    written = comfy_async.fetch_outputs("p-1", tmp_path / "result")
    assert [path.name for path in written] == ["apple_00001_.png"]
    assert written[0].read_bytes() == b"PNGDATA"


def test_fetch_never_polls_a_job_that_has_not_landed(monkeypatch, tmp_path):
    """A fetch that quietly blocked would put the waiting back inside the run
    that just avoided it — so an absent history is an answer, not a wait."""
    gets = wire(monkeypatch, {})
    with pytest.raises(SystemExit) as error:
        comfy_async.fetch_outputs("p-1", tmp_path / "result")
    assert "still queued" in message(error) and "restarted" in message(error)
    assert len(gets) == 1


def test_a_failed_job_is_reported_rather_than_downloaded(monkeypatch, tmp_path):
    history = {"p-1": {"status": {"status_str": "error", "messages": ["boom"]}}}
    wire(monkeypatch, history)
    with pytest.raises(SystemExit) as error:
        comfy_async.fetch_outputs("p-1", tmp_path / "result")
    assert "ComfyUI run failed" in message(error) and "boom" in message(error)


def test_a_finished_job_with_no_files_says_so(monkeypatch, tmp_path):
    wire(monkeypatch, {"p-1": {"status": {"status_str": "success"}, "outputs": {}}})
    with pytest.raises(SystemExit) as error:
        comfy_async.fetch_outputs("p-1", tmp_path / "result")
    assert "no output files" in message(error)


def test_a_missing_comfyui_url_names_the_file_it_belongs_in(monkeypatch):
    monkeypatch.setattr(comfy_async.generate, "load_env", lambda: {})
    with pytest.raises(SystemExit) as error:
        comfy_async.comfyui_url()
    assert "AGFORGE_COMFYUI_URL" in message(error) and ".local/.env" in message(error)


def test_both_comfyui_backed_media_can_be_submitted():
    """Image is deliberately absent: it goes through SwarmUI, which has no
    ComfyUI prompt_id to give, and it returns in seconds anyway."""
    assert set(comfy_async.BACKENDS) == {"video", "music"}
