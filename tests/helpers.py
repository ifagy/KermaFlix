"""Shared helpers for the unit tests: import path, wallets and transaction builders."""

import logging
import os
import sys

NODE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'kerma-node')
if NODE_DIR not in sys.path:
    sys.path.insert(0, NODE_DIR)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from objects import canonical_json  # noqa: E402

# Keep the test output readable.
logging.disable(logging.CRITICAL)

# Example objects from the Task 2 description.
EXAMPLE_COINBASE = {
    'height': 0,
    'outputs': [
        {
            'pubkey': '85acb336a150b16a9c6c8c27a4e9c479d9f99060a7945df0bb1b53365e98969b',
            'value': 50000000000000,
        }
    ],
    'type': 'transaction',
}
EXAMPLE_COINBASE_ID = 'd46d09138f0251edc32e28f1a744cb0b7286850e4c9c777d7e3c6e459b289347'

EXAMPLE_TX = {
    'inputs': [
        {
            'outpoint': {'index': 0, 'txid': EXAMPLE_COINBASE_ID},
            'sig': '6204bbab1b736ce2133c4ea43aff3767c49c881ac80b57ba38a3bab980466644'
                   'cdbacc86b1f4357cfe45e6374b963f5455f26df0a86338310df33e50c15d7f04',
        }
    ],
    'outputs': [
        {
            'pubkey': 'b539258e808b3e3354b9776d1ff4146b52282e864f56224e7e33e7932ec72985',
            'value': 10,
        },
        {
            'pubkey': '8dbcd2401c89c04d6e53c81c90aa0b551cc8fc47c0469217c8f5cfbae1e911f9',
            'value': 49999999999990,
        },
    ],
    'type': 'transaction',
}
EXAMPLE_TX_ID = '895ca2bea390b7508f780c7174900a631e73905dcdc6c07a6b61ede2ebd4033f'


class Wallet:
    """An Ed25519 key pair."""

    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.pubkey = self.key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        ).hex()

    def sign(self, payload: bytes) -> str:
        return self.key.sign(payload).hex()


def coinbase(pubkey: str, value: int = 50 * 10 ** 12, height: int = 0) -> dict:
    """Build a coinbase transaction paying value to pubkey."""
    return {
        'type': 'transaction',
        'height': height,
        'outputs': [{'pubkey': pubkey, 'value': value}],
    }


def spend(inputs, outputs) -> dict:
    """Build and sign a transaction.

    inputs:  list of (txid, index, wallet) - wallet signs the input
    outputs: list of (pubkey, value)
    """
    tx = {
        'type': 'transaction',
        'inputs': [
            {'outpoint': {'txid': txid, 'index': index}, 'sig': None}
            for txid, index, _ in inputs
        ],
        'outputs': [{'pubkey': pk, 'value': v} for pk, v in outputs],
    }
    payload = canonical_json(tx)
    for inp, (_, _, wallet) in zip(tx['inputs'], inputs):
        inp['sig'] = wallet.sign(payload)
    return tx


class DictDB:
    """In-memory stand-in for ObjectDB (only get() is needed for validation)."""

    def __init__(self, *objs):
        from objects import object_id
        self.data = {object_id(o): o for o in objs}

    def get(self, oid):
        return self.data.get(oid)
