"""A Fernet key for tests.

Generated per run rather than committed: a hardcoded key-shaped literal trips
every secret scanner forever (#32), and a test key that lives in git is
indistinguishable from a real one to anybody reading the repo. Module-level so
all tests in a session share one value, which is what the old constant gave.
"""

import base64
import json

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

TEST_FERNET_KEY = Fernet.generate_key().decode()


def device_public_key(private_key: ec.EllipticCurvePrivateKey) -> str:
    return base64.b64encode(private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )).decode()


def unwrap_device_key(wrap_json: str, private_key: ec.EllipticCurvePrivateKey) -> bytes:
    """Independent test peer for the device's ECIES protocol; never logs keys."""
    wrap = json.loads(wrap_json)
    assert wrap["v"] == 1
    assert wrap["alg"] == "ecies-p256-hkdf-aesgcm"
    ephemeral = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), bytes.fromhex(wrap["epk"])
    )
    shared = private_key.exchange(ec.ECDH(), ephemeral)
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"family-cfo-device-wrap"
    ).derive(shared)
    return AESGCM(key).decrypt(bytes.fromhex(wrap["nonce"]), bytes.fromhex(wrap["ct"]), None)
