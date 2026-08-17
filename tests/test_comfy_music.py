"""`agforge music generate` without a ComfyUI.

The same split as `test_comfy_video.py`: the live path is checked by hand;
what is deterministic — and worth pinning — is that a misconfiguration says
so in one clear line, and that the workflow is prepared correctly before
anything is submitted.
"""

import argparse
import json

import pytest

from agforge import comfy_music, generate

WORKFLOW = {
    "94": {"class_type": "TextEncodeAceStepAudio1.5",
           "inputs": {"tags": "the exported test prompt", "seed": 31,
                      "lyrics": "[Instrunment]", "duration": 120}},
    "3": {"class_type": "KSampler", "inputs": {"seed": 31}},
    "104": {"class_type": "SaveAudioMP3", "inputs": {"filename_prefix": "audio/ComfyUI"}},
}


def workflow_file(tmp_path, content=None):
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(WORKFLOW if content is None else content))
    return path


def message(error):
    return str(error.value)


# --- preparing the workflow -------------------------------------------------


def test_the_prompt_lands_in_tags_and_both_seeds_are_fresh(tmp_path):
    """Node ids move on every re-export; the class names do not. The seed
    lives in two nodes — miss either and every run returns the same track."""
    prepared = comfy_music.load_workflow("bach-like 8bit organ", workflow_file(tmp_path))
    assert prepared["94"]["inputs"]["tags"] == "bach-like 8bit organ"
    assert prepared["94"]["inputs"]["seed"] != WORKFLOW["94"]["inputs"]["seed"]
    assert prepared["3"]["inputs"]["seed"] != WORKFLOW["3"]["inputs"]["seed"]


def test_everything_else_in_the_export_is_left_alone(tmp_path):
    """The prompt is the only parameter; lyrics, duration and the rest are
    whatever the exported file carries."""
    prepared = comfy_music.load_workflow("anything", workflow_file(tmp_path))
    assert prepared["94"]["inputs"]["lyrics"] == "[Instrunment]"
    assert prepared["94"]["inputs"]["duration"] == 120


def test_two_runs_of_the_same_prompt_get_different_seeds(tmp_path):
    path = workflow_file(tmp_path)
    seeds = {comfy_music.load_workflow("a tune", path)["3"]["inputs"]["seed"]
             for _ in range(5)}
    assert len(seeds) == 5


def test_a_missing_workflow_file_says_which_file_and_what_shape(tmp_path):
    with pytest.raises(SystemExit) as error:
        comfy_music.load_workflow("a tune", tmp_path / "absent.json")
    assert "absent.json" in message(error) and "API format" in message(error)


def test_a_workflow_without_the_prompt_node_is_refused(tmp_path):
    path = workflow_file(tmp_path, {"1": {"class_type": "SaveAudioMP3", "inputs": {}}})
    with pytest.raises(SystemExit) as error:
        comfy_music.load_workflow("a tune", path)
    assert comfy_music.PROMPT_CLASS in message(error)


def test_an_unreadable_workflow_file_names_the_json_error(tmp_path):
    path = tmp_path / "wf.json"
    path.write_text("{not json")
    with pytest.raises(SystemExit) as error:
        comfy_music.load_workflow("a tune", path)
    assert "not valid JSON" in message(error)


# --- the configuration the run needs ----------------------------------------


def test_a_missing_endpoint_says_which_key_and_which_file(monkeypatch):
    monkeypatch.setattr(generate, "load_env", lambda: {"AGFORGE_S3_BUCKET": "agforge"})
    with pytest.raises(SystemExit) as error:
        comfy_music.run(argparse.Namespace(prompt="a tune", ttl=60))
    assert message(error) == "AGFORGE_COMFYUI_URL missing from .local/.env"


def test_an_empty_prompt_costs_no_call(monkeypatch):
    def no_env():
        raise AssertionError("the prompt should be rejected before anything else")

    monkeypatch.setattr(generate, "load_env", no_env)
    with pytest.raises(SystemExit) as error:
        comfy_music.run(argparse.Namespace(prompt="   ", ttl=60))
    assert message(error) == "prompt is empty"


# --- delivery ---------------------------------------------------------------


def test_music_is_delivered_as_audio():
    """`.mp3` reaching CONTENT_TYPES is what makes the presigned URL play in
    a browser instead of downloading as an octet-stream."""
    assert generate.CONTENT_TYPES[".mp3"] == "audio/mpeg"
