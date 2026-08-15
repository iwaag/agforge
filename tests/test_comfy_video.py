"""`agforge video generate` without a ComfyUI.

The live path is checked by hand (p3 report 2); what is deterministic — and
worth pinning — is that a misconfiguration says so in one clear line instead
of hanging or raising a traceback at an agent, and that the workflow is
prepared correctly before anything is submitted.
"""

import argparse
import json

import pytest

from agforge import comfy_video, generate

WORKFLOW = {
    "129": {"class_type": "RandomNoise", "inputs": {"noise_seed": 47636924935623}},
    "131": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"prompt": "the old one"}},
    "92": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "video/MiniMax_H3"}},
}


def workflow_file(tmp_path, content=None):
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(WORKFLOW if content is None else content))
    return path


def message(error):
    return str(error.value)


# --- preparing the workflow -------------------------------------------------


def test_the_prompt_and_a_fresh_seed_are_injected_by_class_type(tmp_path):
    """Node ids move on every re-export; the class names do not."""
    prepared = comfy_video.load_workflow("a red boat", workflow_file(tmp_path))
    assert prepared["131"]["inputs"]["prompt"] == "a red boat"
    assert prepared["129"]["inputs"]["noise_seed"] != WORKFLOW["129"]["inputs"]["noise_seed"]


def test_two_runs_of_the_same_prompt_get_different_seeds(tmp_path):
    """The exported file carries one fixed seed: left alone, every video of
    a prompt comes out identical."""
    path = workflow_file(tmp_path)
    seeds = {comfy_video.load_workflow("a red boat", path)["129"]["inputs"]["noise_seed"]
             for _ in range(5)}
    assert len(seeds) == 5


def test_a_missing_workflow_file_says_which_file_and_what_shape(tmp_path):
    with pytest.raises(SystemExit) as error:
        comfy_video.load_workflow("a red boat", tmp_path / "absent.json")
    assert "absent.json" in message(error) and "API format" in message(error)


def test_a_workflow_without_the_prompt_node_is_refused(tmp_path):
    path = workflow_file(tmp_path, {"1": {"class_type": "SaveVideo", "inputs": {}}})
    with pytest.raises(SystemExit) as error:
        comfy_video.load_workflow("a red boat", path)
    assert comfy_video.PROMPT_CLASS in message(error)


def test_a_ui_format_export_is_refused_as_such(tmp_path):
    """The UI export is a list of nodes under `nodes`, not `id -> node`."""
    path = workflow_file(tmp_path, {"nodes": [], "links": []})
    with pytest.raises(SystemExit) as error:
        comfy_video.load_workflow("a red boat", path)
    assert comfy_video.PROMPT_CLASS in message(error)


def test_an_unreadable_workflow_file_names_the_json_error(tmp_path):
    path = tmp_path / "wf.json"
    path.write_text("{not json")
    with pytest.raises(SystemExit) as error:
        comfy_video.load_workflow("a red boat", path)
    assert "not valid JSON" in message(error)


# --- the configuration the run needs ----------------------------------------


def test_a_missing_endpoint_says_which_key_and_which_file(monkeypatch):
    monkeypatch.setattr(generate, "load_env", lambda: {"AGFORGE_S3_BUCKET": "agforge"})
    with pytest.raises(SystemExit) as error:
        comfy_video.run(argparse.Namespace(prompt="a red boat", ttl=60))
    assert message(error) == "AGFORGE_COMFYUI_URL missing from .local/.env"


def test_an_empty_prompt_costs_no_call(monkeypatch):
    def no_env():
        raise AssertionError("the prompt should be rejected before anything else")

    monkeypatch.setattr(generate, "load_env", no_env)
    with pytest.raises(SystemExit) as error:
        comfy_video.run(argparse.Namespace(prompt="   ", ttl=60))
    assert message(error) == "prompt is empty"


# --- reading what ComfyUI answered ------------------------------------------


def test_output_references_are_found_whatever_key_the_node_used():
    """`SaveVideo` has changed that key before; the `{filename, subfolder,
    type}` shape is the stable part."""
    entry = {"outputs": {
        "92": {"videos": [{"filename": "MiniMax_H3_00001.mp4",
                           "subfolder": "video", "type": "output"}]},
        "93": {"images": [{"filename": "preview.png", "subfolder": "", "type": "temp"}]},
        "94": {"text": ["not a file"]},
    }}
    assert [item["filename"] for item in comfy_video.output_references(entry)] == [
        "MiniMax_H3_00001.mp4", "preview.png",
    ]


def test_a_video_is_delivered_as_a_video(tmp_path):
    """`.mp4` reaching CONTENT_TYPES is what makes the presigned URL play in
    a browser instead of downloading as an octet-stream."""
    assert generate.CONTENT_TYPES[".mp4"] == "video/mp4"
    assert generate.CONTENT_TYPES[".webm"] == "video/webm"
