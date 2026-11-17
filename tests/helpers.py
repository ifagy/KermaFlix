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

    def add(self, *objs):
        from objects import object_id
        for o in objs:
            self.data[object_id(o)] = o


# ---------- Blocks (Task 3) ----------

TARGET = '0000abc000000000000000000000000000000000000000000000000000000000'
GENESIS_ID = '00002fa163c7dab0991544424b9fd302bb1782b185e5a3bbdf12afb758e57dee'
GENESIS_CREATED = 1671062400
BLOCK_REWARD = 50 * 10 ** 12

# Example objects from the Task 3 description.
EXAMPLE_CB_H1 = {
    'height': 1,
    'outputs': [{
        'pubkey': '3f0bc71a375b574e4bda3ddf502fe1afd99aa020bf6049adfe525d9ad18ff33f',
        'value': 50000000000000,
    }],
    'type': 'transaction',
}
EXAMPLE_CB_H1_ID = '6ebfb4c8e8e9b19dcf54c6ce3e1e143da1f473ea986e70c5cb8899a4671c933a'
EXAMPLE_BLOCK = {
    'T': TARGET,
    'created': 1671148800,
    'miner': 'grader',
    'nonce': '00000000000000000000000000000000000000000000000000000000000463cf',
    'note': 'This block has a coinbase transaction',
    'previd': GENESIS_ID,
    'txids': [EXAMPLE_CB_H1_ID],
    'type': 'block',
}
EXAMPLE_BLOCK_ID = '000020cb0002575a71955763adf365c78182f0bb5bee767794ebc7346e0a2194'

_NONCE_MARKER = 'n' * 64  # not hex, so it cannot collide with other values


def mine(block: dict) -> dict:
    """Return a copy of block with a nonce that satisfies the proof-of-work."""
    import hashlib
    import random
    template = canonical_json(dict(block, nonce=_NONCE_MARKER))
    prefix, suffix = template.split(_NONCE_MARKER.encode())
    base = hashlib.blake2s(prefix)
    target = bytes.fromhex(TARGET)
    n = random.getrandbits(64) << 128
    while True:
        nonce = b'%064x' % n
        h = base.copy()
        h.update(nonce + suffix)
        if h.digest() < target:
            return dict(block, nonce=nonce.decode())
        n += 1


def unmine(block: dict) -> dict:
    """Return a copy of block whose proof-of-work is NOT valid."""
    import hashlib
    target = bytes.fromhex(TARGET)
    n = 0
    while True:
        candidate = dict(block, nonce='%064x' % n)
        if hashlib.blake2s(canonical_json(candidate)).digest() >= target:
            return candidate
        n += 1


def make_block(txids=(), previd=GENESIS_ID, created=None, do_mine=True, **extra) -> dict:
    """Build a block (mined by default). created defaults to one second after genesis."""
    block = {
        'type': 'block',
        'txids': list(txids),
        'nonce': '0' * 64,
        'previd': previd,
        'created': GENESIS_CREATED + 1 if created is None else created,
        'T': TARGET,
        'miner': 'unit-test',
    }
    block.update(extra)
    return mine(block) if do_mine else block
