"""Provider selection, retries, fallback and provider-specific payloads."""

import pytest

from src import config, notifier

from .conftest import FakeResponse


def use_callmebot(monkeypatch):
    monkeypatch.setattr(config, "CALLMEBOT_PHONE", "+919800000000")
    monkeypatch.setattr(config, "CALLMEBOT_APIKEY", "1234567")


def use_telegram(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")


def use_meta(monkeypatch, template="ipo_daily_alert"):
    monkeypatch.setattr(config, "META_WA_TOKEN", "EAAG-token")
    monkeypatch.setattr(config, "META_WA_PHONE_NUMBER_ID", "1111")
    monkeypatch.setattr(config, "META_WA_TO", "+91 98000 00000")
    monkeypatch.setattr(config, "META_WA_TEMPLATE", template)


# ----------------------------------------------------------------- selection


def test_nothing_configured_is_a_clear_error():
    with pytest.raises(notifier.PermanentError, match="No notification provider"):
        notifier.resolve_provider()


def test_auto_prefers_whatsapp_over_telegram(monkeypatch):
    use_telegram(monkeypatch)
    assert notifier.resolve_provider() == "telegram"
    use_callmebot(monkeypatch)
    assert notifier.resolve_provider() == "callmebot"
    use_meta(monkeypatch)
    assert notifier.resolve_provider() == "meta"


def test_telegram_is_flagged_as_not_whatsapp(monkeypatch, caplog):
    use_telegram(monkeypatch)
    notifier.resolve_provider()
    assert "NOT WhatsApp" in caplog.text


def test_explicit_provider_without_secrets_fails(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_PROVIDER", "callmebot")
    with pytest.raises(notifier.PermanentError, match="secrets are missing"):
        notifier.resolve_provider()


def test_describe_config_never_leaks_secrets(monkeypatch):
    use_callmebot(monkeypatch)
    text = "\n".join(notifier.describe_config())
    assert "1234567" not in text and "+9198" not in text
    assert "callmebot  configured" in text


# ----------------------------------------------------------------- payloads


def test_chunking_splits_on_blocks_and_numbers_parts():
    blocks = [f"Company {i}\n- GMP: ₹{i}\n---" for i in range(60)]
    chunks = notifier.chunk_message("\n\n".join(blocks), limit=300)
    assert len(chunks) > 1 and all(len(c) <= 310 for c in chunks)
    assert chunks[0].startswith(f"(1/{len(chunks)})")


def test_meta_template_parameter_has_no_newlines(monkeypatch):
    use_meta(monkeypatch)
    payload = notifier.meta_payload("Line one\n\nLine    two\nLine\tthree")
    param = payload["template"]["components"][0]["parameters"][0]["text"]
    assert "\n" not in param and "\t" not in param and "  " not in param
    assert payload["to"] == "919800000000"
    assert payload["template"]["name"] == "ipo_daily_alert"


def test_meta_without_template_sends_text(monkeypatch):
    use_meta(monkeypatch, template="")
    assert notifier.meta_payload("hi")["type"] == "text"


# ----------------------------------------------------------------- delivery


def test_retry_then_success(monkeypatch):
    use_telegram(monkeypatch)
    replies = iter([FakeResponse(502, text="bad gateway"), FakeResponse(200, {"ok": True})])
    monkeypatch.setattr(notifier.requests, "post", lambda *a, **k: next(replies))
    assert notifier.send_whatsapp("hello") == "telegram"


def test_bad_credentials_are_not_retried(monkeypatch):
    use_meta(monkeypatch)
    calls = []

    def post(*a, **k):
        calls.append(1)
        return FakeResponse(401, {"error": {"message": "Invalid OAuth access token"}})

    monkeypatch.setattr(notifier.requests, "post", post)
    with pytest.raises(notifier.NotifierError, match="HTTP 401"):
        notifier.send_whatsapp("hello")
    assert len(calls) == 1


def test_callmebot_error_inside_http_200_is_caught(monkeypatch):
    use_callmebot(monkeypatch)
    monkeypatch.setattr(notifier.requests, "get",
                        lambda *a, **k: FakeResponse(200, text="<p>APIKey is invalid.</p>"))
    with pytest.raises(notifier.NotifierError, match="APIKey is invalid"):
        notifier.send_whatsapp("hello")


def test_fallback_delivers_but_still_signals_failure(monkeypatch):
    use_callmebot(monkeypatch)
    use_telegram(monkeypatch)
    monkeypatch.setattr(config, "WHATSAPP_PROVIDER", "callmebot")
    monkeypatch.setattr(config, "FALLBACK_PROVIDER", "telegram")
    sent = []
    monkeypatch.setattr(notifier.requests, "get",
                        lambda *a, **k: FakeResponse(503, text="Service Unavailable"))

    def post(url, json=None, **k):
        sent.append(json["text"])
        return FakeResponse(200, {"ok": True})

    monkeypatch.setattr(notifier.requests, "post", post)
    with pytest.raises(notifier.DeliveredViaFallback) as err:
        notifier.send_whatsapp("today's IPOs")
    assert err.value.provider == "telegram"
    assert sent and "today's IPOs" in sent[0] and "callmebot" in sent[0]


def test_meta_success(monkeypatch):
    use_meta(monkeypatch)
    seen = {}

    def post(url, headers=None, json=None, **k):
        seen.update(url=url, auth=headers["Authorization"], json=json)
        return FakeResponse(200, {"messages": [{"id": "wamid.X", "message_status": "accepted"}]})

    monkeypatch.setattr(notifier.requests, "post", post)
    assert notifier.send_whatsapp("hello") == "meta"
    assert seen["url"].endswith("/1111/messages") and seen["auth"] == "Bearer EAAG-token"
