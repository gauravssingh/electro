"""Cryptographic utilities for ELNet communication.

AES/CBC/PKCS7Padding payload encryption matching decompiled Utility.java behavior.
"""

from typing import Dict, Any, Union
import base64
import json
import re

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


def validate_keys(skey: bytes, ivkey: bytes) -> None:
    """Validate AES key and IV byte lengths."""
    if len(skey) != 32:
        raise ValueError(f"Invalid AES key length: expected 32 bytes, got {len(skey)}")
    if len(ivkey) != 16:
        raise ValueError(f"Invalid AES IV length: expected 16 bytes, got {len(ivkey)}")


def encrypt_payload(payload: Dict[str, Any], skey: bytes, ivkey: bytes) -> str:
    """Serialize payload to compact UTF-8 JSON, encrypt with AES-CBC PKCS7, and return Base64."""
    validate_keys(skey, ivkey)
    plaintext = json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    cipher = AES.new(skey, AES.MODE_CBC, ivkey)
    ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))
    return base64.b64encode(ciphertext).decode("ascii")


def decrypt_payload(b64_ciphertext: str, skey: bytes, ivkey: bytes) -> Dict[str, Any]:
    """Base64 decode, AES-CBC decrypt with PKCS7 unpadding, and parse JSON payload."""
    validate_keys(skey, ivkey)
    raw_ciphertext = base64.b64decode(b64_ciphertext)
    cipher = AES.new(skey, AES.MODE_CBC, ivkey)
    decrypted_padded = cipher.decrypt(raw_ciphertext)
    plaintext = unpad(decrypted_padded, AES.block_size)
    return json.loads(plaintext.decode("utf-8"))


def wrap_envelope(b64_ciphertext: str) -> Dict[str, str]:
    """Wrap encrypted Base64 string in ELNet InputType envelope."""
    return {"InputType": b64_ciphertext}


def redact_sensitive(text: str) -> str:
    """Redact passwords, bearer tokens, or sensitive values from logs/strings."""
    if not text:
        return text
    # Redact Bearer tokens
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    # Redact AuthToken or Password fields in JSON-like strings
    text = re.sub(r'("?(?:AuthToken|Password|RefreshToken|ELNET_SKEY|ELNET_IVKEY)"?\s*[:=]\s*"?)[^",\s}]+("?)', r'\1[REDACTED]\2', text, flags=re.IGNORECASE)
    return text
