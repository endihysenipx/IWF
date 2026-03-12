from types import SimpleNamespace

from PIL import Image

from app import ocr_extractor


def _build_fake_client():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0),
    )
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_: response),
        )
    )


def test_extract_data_from_scanned_pdf_omits_poppler_path_when_unset(monkeypatch):
    call = {}

    def fake_convert_from_bytes(payload, **kwargs):
        call["payload"] = payload
        call["kwargs"] = kwargs
        return [Image.new("RGB", (16, 16))]

    monkeypatch.setattr(ocr_extractor, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ocr_extractor, "client", _build_fake_client())
    monkeypatch.setattr(ocr_extractor, "POPPLER_PATH", None)
    monkeypatch.setattr(ocr_extractor, "MAX_AB_PAGES", 0)
    monkeypatch.setattr(ocr_extractor, "convert_from_bytes", fake_convert_from_bytes)

    result = ocr_extractor.extract_data_from_scanned_pdf(b"%PDF-1.7 fake")

    assert result["data"] == {}
    assert call["payload"] == b"%PDF-1.7 fake"
    assert "poppler_path" not in call["kwargs"]


def test_extract_data_from_scanned_pdf_passes_poppler_path_when_configured(monkeypatch):
    call = {}

    def fake_convert_from_bytes(payload, **kwargs):
        call["payload"] = payload
        call["kwargs"] = kwargs
        return [Image.new("RGB", (16, 16))]

    monkeypatch.setattr(ocr_extractor, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(ocr_extractor, "client", _build_fake_client())
    monkeypatch.setattr(ocr_extractor, "POPPLER_PATH", "/usr/bin")
    monkeypatch.setattr(ocr_extractor, "MAX_AB_PAGES", 0)
    monkeypatch.setattr(ocr_extractor, "convert_from_bytes", fake_convert_from_bytes)

    result = ocr_extractor.extract_data_from_scanned_pdf(b"%PDF-1.7 fake")

    assert result["data"] == {}
    assert call["payload"] == b"%PDF-1.7 fake"
    assert call["kwargs"] == {"poppler_path": "/usr/bin"}
