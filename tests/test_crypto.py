"""Unit tests for elnet/crypto.py."""

import json
import pytest

from elnet.crypto import (
    encrypt_payload,
    decrypt_payload,
    wrap_envelope,
    validate_keys,
    redact_sensitive,
)


@pytest.fixture
def synthetic_keys():
    skey = b"0123456789abcdef0123456789abcdef"  # exactly 32 bytes
    ivkey = b"0123456789abcdef"                  # exactly 16 bytes
    return skey, ivkey


def test_key_validation(synthetic_keys):
    skey, ivkey = synthetic_keys
    # Valid keys should not raise
    validate_keys(skey, ivkey)

    # Invalid skey length
    with pytest.raises(ValueError, match="Invalid AES key length"):
        validate_keys(b"short_key", ivkey)

    # Invalid ivkey length
    with pytest.raises(ValueError, match="Invalid AES IV length"):
        validate_keys(skey, b"short_iv")


def test_encrypt_decrypt_roundtrip(synthetic_keys):
    skey, ivkey = synthetic_keys
    payload = {
        "MeterID": 1042,
        "Access": "Live",
        "Nested": {"key": "value", "count": 42},
    }

    encrypted_b64 = encrypt_payload(payload, skey, ivkey)
    assert isinstance(encrypted_b64, str)
    assert len(encrypted_b64) > 0

    decrypted = decrypt_payload(encrypted_b64, skey, ivkey)
    assert decrypted == payload


def test_wrap_envelope():
    envelope = wrap_envelope("dummy_base64_string")
    assert envelope == {"InputType": "dummy_base64_string"}


def test_redact_sensitive():
    sample = 'Authorization: Bearer secret_token_abc123, {"Password": "secret_password"}'
    redacted = redact_sensitive(sample)
    assert "secret_token_abc123" not in redacted
    assert "secret_password" not in redacted
    assert "[REDACTED]" in redacted
