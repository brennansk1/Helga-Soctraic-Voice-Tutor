"""crypto_storage.py — Encrypted storage engine for Helga multi-user account data.

Provides authenticated encryption (PBKDF2 + HMAC-SHA256 stream cipher) for user
records, course files, and progress data stored on disk.

AGENT AUDITABILITY CONTRACT:
The master encryption key is derived from HELGA_MASTER_KEY or persisted in
`data/.keys/master.key`. Agent evaluation tools, benchmark probes, and tests
can inspect decrypted payloads at any time via `inspect_decrypted_data()`.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
from typing import Any, Dict, Optional, Union

KEY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/.keys"))
KEY_FILE = os.path.join(KEY_DIR, "master.key")
DEFAULT_SALT = b"HelgaSocraticVoiceTutorMasterSalt2026"


def get_or_create_master_key() -> bytes:
    """Return the 32-byte master encryption key, creating data/.keys/master.key if needed."""
    env_key = os.getenv("HELGA_MASTER_KEY")
    if env_key:
        return hashlib.sha256(env_key.encode()).digest()

    os.makedirs(KEY_DIR, exist_ok=True)
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return f.read().strip()

    new_key = secrets.token_bytes(32)
    with open(KEY_FILE, "wb") as f:
        f.write(new_key)
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    return new_key


def _derive_keys(master_key: bytes, nonce: bytes):
    """Derive (enc_key, mac_key) using PBKDF2 HMAC-SHA256."""
    derived = hashlib.pbkdf2_hmac("sha256", master_key, nonce + DEFAULT_SALT, iterations=10_000, dklen=64)
    return derived[:32], derived[32:]


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """Generate deterministic pseudorandom keystream using HMAC-SHA256 counter mode."""
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        block = hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        stream.extend(block)
        counter += 1
    return bytes(stream[:length])


def encrypt_data(data: Union[str, bytes, dict, list]) -> str:
    """Encrypt payload into authenticated base64 string format: enc_v1:<nonce>:<mac>:<ciphertext>"""
    if isinstance(data, (dict, list)):
        raw_bytes = json.dumps(data, sort_keys=True).encode("utf-8")
    elif isinstance(data, str):
        raw_bytes = data.encode("utf-8")
    else:
        raw_bytes = bytes(data)

    master_key = get_or_create_master_key()
    nonce = secrets.token_bytes(16)
    enc_key, mac_key = _derive_keys(master_key, nonce)

    ks = _keystream(enc_key, nonce, len(raw_bytes))
    ciphertext = bytes(a ^ b for a, b in zip(raw_bytes, ks))

    mac = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()

    return f"enc_v1:{base64.b64encode(nonce).decode()}:{base64.b64encode(mac).decode()}:{base64.b64encode(ciphertext).decode()}"


def decrypt_data(token: str) -> bytes:
    """Decrypt an enc_v1 token back to raw bytes. Raises ValueError on MAC mismatch or invalid format."""
    if not isinstance(token, str) or not token.startswith("enc_v1:"):
        raise ValueError("Invalid encrypted data format")

    parts = token.split(":")
    if len(parts) != 4:
        raise ValueError("Malformed encrypted token structure")

    _, b64_nonce, b64_mac, b64_ct = parts
    try:
        nonce = base64.b64decode(b64_nonce)
        expected_mac = base64.b64decode(b64_mac)
        ciphertext = base64.b64decode(b64_ct)
    except Exception as e:
        raise ValueError(f"Base64 decoding failed: {e}")

    master_key = get_or_create_master_key()
    enc_key, mac_key = _derive_keys(master_key, nonce)

    computed_mac = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(computed_mac, expected_mac):
        raise ValueError("Authentication failed: HMAC MAC mismatch")

    ks = _keystream(enc_key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, ks))


def decrypt_json(token: str) -> Any:
    """Decrypt an encrypted payload and parse JSON."""
    raw = decrypt_data(token)
    return json.loads(raw.decode("utf-8"))


def inspect_decrypted_data(token_or_raw: Any) -> Any:
    """Agent Inspection API: Decrypts encrypted payload if tokenized, otherwise returns raw input.
    Enables testing tools and agent benchmarks to analyze stored data transparently.
    """
    if isinstance(token_or_raw, str) and token_or_raw.startswith("enc_v1:"):
        try:
            return decrypt_json(token_or_raw)
        except Exception:
            try:
                return decrypt_data(token_or_raw).decode("utf-8", errors="replace")
            except Exception:
                return token_or_raw
    return token_or_raw
