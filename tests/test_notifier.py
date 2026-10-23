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


# ----------------------------------------------------------------- telegram: many chats


def telegram_recorder(monkeypatch, replies):
    """Fake Telegram API. `replies` maps chat_id -> list of FakeResponse to
    return in order (last one repeats)."""
    calls = []

    def post(url, json=None, **_):
        calls.append(json["chat_id"])
        queue = replies[json["chat_id"]]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(notifier.requests, "post", post)
    return calls


def test_extra_chat_ids_all_receive(monkeypatch):
    use_telegram(monkeypatch)  # personal chat "42"
    monkeypatch.setattr(config, "TELEGRAM_EXTRA_CHAT_IDS", "-1001234567890, 42")
    ok = FakeResponse(200, {"ok": True})
    calls = telegram_recorder(monkeypatch, {"42": [ok], "-1001234567890": [ok]})
    assert notifier.telegram_chat_ids() == ["42", "-1001234567890"]  # de-duplicated
    assert notifier.send_whatsapp("hello") == "telegram"
    assert calls == ["42", "-1001234567890"]


def test_one_chat_failing_does_not_resend_to_the_other(monkeypatch):
    use_telegram(monkeypatch)
    monkeypatch.setattr(config, "TELEGRAM_EXTRA_CHAT_IDS", "-100999")
    calls = telegram_recorder(monkeypatch, {
        "42": [FakeResponse(200, {"ok": True})],
        "-100999": [FakeResponse(400, {"ok": False, "description": "chat not found"})],
    })
    with pytest.raises(notifier.PartialDelivery) as err:
        notifier.send_whatsapp("hello")
    assert err.value.provider == "telegram"
    assert calls.count("42") == 1 and calls.count("-100999") == 1  # 400 = no retry


def test_transient_error_retries_only_that_chat(monkeypatch):
    use_telegram(monkeypatch)
    monkeypatch.setattr(config, "TELEGRAM_EXTRA_CHAT_IDS", "-100999")
    ok = FakeResponse(200, {"ok": True})
    calls = telegram_recorder(monkeypatch, {
        "42": [ok], "-100999": [FakeResponse(502, text="bad gateway"), ok]})
    assert notifier.send_whatsapp("hello") == "telegram"
    assert calls == ["42", "-100999", "-100999"]


def test_upgraded_group_is_followed_to_its_new_id(monkeypatch, caplog):
    use_telegram(monkeypatch)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "-4001")
    migrated = FakeResponse(400, {"ok": False, "error_code": 400,
                                  "description": "Bad Request: group chat was upgraded to a supergroup chat",
                                  "parameters": {"migrate_to_chat_id": -1004001}})
    calls = telegram_recorder(monkeypatch, {"-4001": [migrated],
                                            -1004001: [FakeResponse(200, {"ok": True})]})
    assert notifier.send_whatsapp("hello") == "telegram"
    assert calls == ["-4001", -1004001]
    assert "new ID is -1004001" in caplog.text
