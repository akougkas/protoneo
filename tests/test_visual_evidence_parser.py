from protoneo.knowledge import visual_evidence as ve


def test_sanitize_strips_reasoning_and_markdown():
    raw = "<think>the user wants...</think>\n### Chart Type\n**Figure 1** shows speedup of 4.2x."
    out = ve.sanitize_description(raw)
    assert "<think>" not in out and "think" not in out.lower().split()[0:1]
    assert "###" not in out
    assert "4.2x" in out


def test_extract_numeric_claims():
    text = "VisionHPC achieves 4.2x speedup over OpenMP and reaches 87% of peak at 1024 nodes."
    claims = ve.extract_numeric_claims(text)
    assert any("4.2x" in c for c in claims)
    assert any("87%" in c for c in claims)
    assert any("1024" in c for c in claims)


def test_describe_image_builds_provenance(monkeypatch, tmp_path):
    img = tmp_path / "fig.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    def fake_post(url, json, timeout):  # noqa: A002
        class R:
            status_code = 200

            def json(self_inner):
                return {
                    "choices": [
                        {"message": {"content": "<think>x</think>Bar chart; 4.2x speedup."}}
                    ]
                }

            def raise_for_status(self_inner):
                pass

        assert url.endswith("/v1/chat/completions")
        content = json["messages"][0]["content"]
        assert any(p.get("type") == "image_url" for p in content)
        return R()

    monkeypatch.setattr(ve.httpx, "post", fake_post)
    rec = ve.describe_image(
        str(img),
        vlm_config={"url": "http://h/v1/chat/completions", "model": "omni", "prompt": "Describe."},
        kind="table",
    )
    assert rec["description"] == "Bar chart; 4.2x speedup."
    assert rec["description_source"] == "vlm"
    assert rec["model"] == "omni"
    assert rec["endpoint"] == "http://h/v1/chat/completions"
    assert rec["prompt"] == "Describe."
    assert rec["kind"] == "table"
    assert "4.2x speedup" in rec["numeric_claims"][0]
    assert 0.0 <= rec["confidence"] <= 1.0


def test_describe_image_handles_failure(monkeypatch, tmp_path):
    img = tmp_path / "fig.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ve.httpx, "post", boom)
    rec = ve.describe_image(
        str(img),
        vlm_config={"url": "http://h/v1/chat/completions", "model": "omni"},
        kind="figure",
    )
    assert rec["description"] == ""
    assert rec["description_source"] == "error"
    assert "connection refused" in rec["error"]
