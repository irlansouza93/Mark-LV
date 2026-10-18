"""core/redact.py — secrets never reach disk (spec SC-003, research R4)."""
import copy
import json

from core.redact import load_config_secrets, mask, redact

GEMINI = "AIza" + "Sy" + "a" * 33          # 4 + 35 chars, the Google key shape
OPENAI = "sk-proj-" + "b" * 30
ANTHROPIC = "sk-ant-" + "c" * 30
TELEGRAM = "123456789:" + "d" * 35


def test_mask_shows_four_first_and_two_last():
    assert mask(GEMINI) == f"‹redacted:{GEMINI[:4]}…{GEMINI[-2:]}›"
    assert mask("AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXf3") == "‹redacted:AIza…f3›"


def test_mask_hides_short_values_completely():
    # 4 + 2 of a 6-char value would reveal all of it.
    assert mask("abc123") == "‹redacted›"
    assert mask("") == "‹redacted›"


def test_literal_secret_is_masked_in_str_dict_list_and_traceback():
    secret = "my-very-private-token-0123456789"
    text = f"call failed for {secret} twice"
    assert secret not in redact(text, [secret])
    nested = {"a": [f"x {secret}", {"b": f"y{secret}z"}], "c": (f"{secret}",)}
    out = json.dumps(redact(nested, [secret]), ensure_ascii=False)
    assert secret not in out
    tb = ('Traceback (most recent call last):\n  File "x.py", line 1\n'
          f'    do(\'{secret}\')\nValueError: bad {secret}\n')
    assert secret not in redact(tb, [secret])


def test_known_formats_are_masked_without_any_secrets_list():
    for s in (GEMINI, OPENAI, ANTHROPIC, TELEGRAM):
        out = redact(f"error talking to service with {s} inside")
        assert s not in out, s
        assert "‹redacted:" in out


def test_bearer_authorization_and_url_params():
    tok = "ya29.a0AfH6SMB" + "e" * 20
    assert tok not in redact(f"Bearer {tok}")
    assert "Bearer" in redact(f"Bearer {tok}")        # the word stays, the token goes
    out = redact(f"Authorization: Basic dXNlcjpwYXNzd29yZA==\nnext line")
    assert "dXNlcjpwYXNzd29yZA" not in out and "next line" in out
    url = "https://x.test/v1?model=a&key=SECRETVALUE1234&token=OTHERVALUE5678&access_token=THIRD99999"
    out = redact(url)
    for leaked in ("SECRETVALUE1234", "OTHERVALUE5678", "THIRD99999"):
        assert leaked not in out
    assert "model=a" in out and "key=" in out


def test_sensitive_dict_keys_mask_the_whole_string_value():
    data = {
        "api_key": "short", "Token": "t0k", "client_secret": "s", "password": "hunter2",
        "Authorization": "whatever", "cookie": "sid=1", "name": "visible",
    }
    out = redact(data)
    for k in ("api_key", "Token", "client_secret", "password", "Authorization", "cookie"):
        assert str(out[k]).startswith("‹redacted"), k
    assert out["name"] == "visible"


def test_key_is_only_sensitive_as_a_final_or_isolated_word():
    # "keys: ctrl+c" (keyboard action) and "hotkey"/"monkey" must stay readable for triage.
    harmless = {"keys": "ctrl+c", "hotkey": "ctrl+alt+j", "monkey": "banana",
                "keyboard": "us", "key_count": "3"}
    assert redact(harmless) == harmless
    for name in ("key", "api_key", "apiKey", "gemini_api_key", "x-api-key", "API_KEY",
                 "APIKEY", "private_key"):
        out = redact({name: "value-that-is-secret"})
        assert out[name].startswith("‹redacted"), name


def test_other_sensitive_words_match_anywhere_in_the_name():
    for name in ("bot_token", "tokenizer_note", "client_secret", "db_password",
                 "Authorization", "set_cookie", "mypasswordhint"):
        assert redact({name: "plain text value"})[name].startswith("‹redacted"), name


def test_sensitive_key_names_do_not_swallow_numbers_or_containers():
    # UsageRecord has a "tokens" object: it must survive redaction intact.
    rec = {"tokens": {"in_text": 120, "out_text": 30}, "max_tokens": 256, "ok": True}
    assert redact(rec) == rec


def test_plain_text_is_untouched_and_input_not_mutated():
    assert redact("Opened Excel in 1.2 s") == "Opened Excel in 1.2 s"
    assert redact(None) is None and redact(42) == 42
    original = {"k": [f"see {GEMINI}", {"password": "pw"}]}
    snapshot = copy.deepcopy(original)
    redact(original, ["pw"])
    assert original == snapshot


def test_secrets_list_ignores_empty_and_tiny_values():
    # An empty/1-char "secret" would otherwise mangle every string.
    assert redact("hello world", ["", "o"]) == "hello world"


def test_load_config_secrets_keeps_only_long_strings(tmp_path):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({
        "gemini_api_key": GEMINI,
        "short": "abc",
        "flag": True,
        "count": 12345678901234567890,
        "plugins": {"telegram": {"bot_token": TELEGRAM}, "list": ["z" * 16, "no"]},
        "plugin_config": {"telegram": {"chat": "c" * 16}},
    }), encoding="utf-8")
    got = load_config_secrets(cfg)
    # Sensitive names (bot_token, gemini_api_key) and everything under plugin_config; "list" is neither.
    assert set(got) == {GEMINI, TELEGRAM, "c" * 16}


def test_load_config_secrets_never_raises(tmp_path):
    assert load_config_secrets(tmp_path / "missing.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_config_secrets(bad) == []
