"""`agforge image generate --init-image`: a reference image steers SwarmUI.

Pinned: the file goes to SwarmUI as a data URL under `initimage` with the
creativity fraction beside it, the request carries both alongside the
ordinary parameters, and a fraction outside 0..1 or an unreadable file
stops the command with a sentence.
"""

import pytest

from agforge import generate


def test_init_image_payload_is_a_data_url_and_a_fraction(tmp_path):
    image = tmp_path / "meadow.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    payload = generate.init_image_payload(image, 0.4)
    assert payload["initimage"].startswith("data:image/png;base64,")
    assert payload["initimagecreativity"] == 0.4


def test_out_of_range_creativity_and_missing_file_stop_with_a_sentence(tmp_path):
    image = tmp_path / "meadow.png"
    image.write_bytes(b"x")
    with pytest.raises(SystemExit, match="between 0 and 1"):
        generate.init_image_payload(image, 1.5)
    with pytest.raises(SystemExit, match="cannot read --init-image"):
        generate.init_image_payload(tmp_path / "absent.png", 0.5)


def test_generate_sends_the_reference_with_the_parameters(monkeypatch, tmp_path):
    sent = {}

    class Response:
        def __init__(self, data):
            self._data = data
            self.content = b"png"

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def post(url, json=None, timeout=None):
        if url.endswith("/API/GetNewSession"):
            return Response({"session_id": "s1"})
        sent.update(json)
        return Response({"images": ["View/local/raw/out.png"]})

    monkeypatch.setattr(generate.requests, "post", post)
    monkeypatch.setattr(generate.requests, "get", lambda url, timeout=None: Response({}))
    monkeypatch.setattr(generate, "OUT_DIR", tmp_path)
    generate.generate_image("http://swarm", "dusk", {"model": "m", "steps": "4"},
                            {"initimage": "data:image/png;base64,QUJD", "initimagecreativity": 0.6})
    assert sent["initimage"] == "data:image/png;base64,QUJD"
    assert sent["initimagecreativity"] == 0.6
    assert sent["model"] == "m" and sent["prompt"] == "dusk"
