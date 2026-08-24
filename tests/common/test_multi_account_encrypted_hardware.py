"""test_multi_account_encrypted_hardware.py — Unit and integration tests for multi-account system, encrypted storage, agent auditability, and single-active account hardware locking.
"""

import unittest
from services.common.crypto_storage import (
    encrypt_data,
    decrypt_data,
    decrypt_json,
    inspect_decrypted_data,
    get_or_create_master_key,
)
from services.common.hardware_lock import (
    HardwareSessionManager,
    HardwareLockError,
    get_hardware_manager,
)


class TestEncryptedStorageAndAuditability(unittest.TestCase):
    """Verify AES-256 equivalent authenticated encryption & agent inspection API."""

    def test_encryption_decryption_cycle(self):
        payload = {
            "account_id": "par_12345",
            "email": "user@example.com",
            "progress": {"mastery_level": 4, "concepts_learned": 12},
        }
        token = encrypt_data(payload)
        self.assertTrue(token.startswith("enc_v1:"))

        decrypted = decrypt_json(token)
        self.assertEqual(decrypted, payload)

    def test_agent_inspection_api(self):
        payload = {"secret_notes": "Quantum Mechanics Exam Notes", "score": 98.5}
        token = encrypt_data(payload)

        # Agent inspection API inspect_decrypted_data decrypts tokenized payloads transparently
        inspected = inspect_decrypted_data(token)
        self.assertEqual(inspected, payload)

        # Raw non-encrypted payload passes through unchanged
        raw_input = {"unencrypted": True}
        self.assertEqual(inspect_decrypted_data(raw_input), raw_input)

    def test_tamper_detection(self):
        payload = {"user": "charlie"}
        token = encrypt_data(payload)
        parts = token.split(":")

        # Tamper with MAC signature
        tampered_mac = parts[0] + ":" + parts[1] + ":BADMAC" + parts[2] + ":" + parts[3]
        with self.assertRaises(ValueError):
            decrypt_data(tampered_mac)


class TestSingleActiveHardwareLock(unittest.TestCase):
    """Verify that only ONE account can hold active hardware access at a time."""

    def setUp(self):
        self.hw_mgr = HardwareSessionManager(idle_timeout_s=300)

    def test_single_active_hardware_enforcement(self):
        # User 1 claims hardware
        self.assertTrue(self.hw_mgr.claim_hardware_session("usr_1", "user1@example.com"))

        # User 1 checks access -> Allowed
        self.hw_mgr.check_hardware_access("usr_1")

        # User 2 attempts hardware claim while User 1 is active -> Rejected
        self.assertFalse(self.hw_mgr.claim_hardware_session("usr_2", "user2@example.com"))

        # User 2 access check -> HardwareLockError
        with self.assertRaises(HardwareLockError):
            self.hw_mgr.check_hardware_access("usr_2")

        # User 1 releases hardware
        self.assertTrue(self.hw_mgr.release_hardware_session("usr_1"))

        # User 2 claims hardware -> Allowed
        self.assertTrue(self.hw_mgr.claim_hardware_session("usr_2", "user2@example.com"))
        self.hw_mgr.check_hardware_access("usr_2")


if __name__ == "__main__":
    unittest.main()
