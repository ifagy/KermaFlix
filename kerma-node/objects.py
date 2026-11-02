"""Application objects: canonical encoding, object ids, syntax and transaction validation."""

import copy
import hashlib
import json
import re
from typing import Any, Dict

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Error names that do not mean the communication partner is faulty.
NON_FATAL_ERRORS = {'UNKNOWN_OBJECT', 'UNFINDABLE_OBJECT'}

# Lower-case hex strings of a fixed length.
HASH_RE = re.compile(r'[0-9a-f]{64}')  # objectids, txids and public keys (32 bytes)
SIG_RE = re.compile(r'[0-9a-f]{128}')  # Ed25519 signatures (64 bytes)

GENESIS_BLOCK = {
    'T': '0000abc000000000000000000000000000000000000000000000000000000000',
    'created': 1671062400,
    'miner': 'Marabu',
    'nonce': '00000000000000000000000000000000000000000000000000000000005bb0f2',
    'note': 'The New York Times 2022-12-13: Scientists Achieve Nuclear Fusion '
            'Breakthrough With Blast of 192 Lasers',
    'previd': None,
    'txids': [],
    'type': 'block',
}
GENESIS_ID = '00002fa163c7dab0991544424b9fd302bb1782b185e5a3bbdf12afb758e57dee'


class KermaError(Exception):
    """A protocol error that is reported to the peer with an error message."""

    def __init__(self, name: str, msg: str):
        """Store the standardized error name and a human readable explanation."""
        super().__init__(f'{name}: {msg}')
        self.name = name
        self.msg = msg

    @property
    def fatal(self) -> bool:
        """True if the error means the communication partner is faulty."""
        return self.name not in NON_FATAL_ERRORS


# ---------- Encoding and ids ----------

def canonical_json(obj: Any) -> bytes:
    """Encode obj as canonical JSON (sorted keys, no whitespace), without a newline."""
    return json.dumps(
        obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    ).encode('utf-8')


def object_id(obj: Any) -> str:
    """Return the objectid: the hex blake2s hash of the canonical JSON of obj."""
    return hashlib.blake2s(canonical_json(obj)).hexdigest()


def is_hash(value: Any) -> bool:
    """True if value is a 32-byte lower-case hex string."""
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


def is_signature(value: Any) -> bool:
    """True if value is a 64-byte lower-case hex string."""
    return isinstance(value, str) and SIG_RE.fullmatch(value) is not None


def is_non_negative_int(value: Any) -> bool:
    """True if value is an integer >= 0 (booleans are rejected)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# ---------- Syntax checks ----------

def _format_error(msg: str) -> KermaError:
    return KermaError('INVALID_FORMAT', msg)


def _check_keys(obj: Any, expected: set, what: str) -> None:
    """Raise INVALID_FORMAT unless obj is a dict with exactly the expected keys."""
    if not isinstance(obj, dict):
        raise _format_error(f'{what} is not a JSON object')
    missing = expected - set(obj)
    if missing:
        raise _format_error(f'{what} is missing keys: {sorted(missing)}')
    extra = set(obj) - expected
    if extra:
        raise _format_error(f'{what} has additional keys: {sorted(extra)}')


def validate_transaction_syntax(tx: Any) -> None:
    """Check the structure of a transaction object; raise INVALID_FORMAT on errors.

    A coinbase transaction has the keys type, height and outputs; a normal
    transaction has the keys type, inputs (non-empty) and outputs.
    """
    if not isinstance(tx, dict):
        raise _format_error('Transaction is not a JSON object')
    if tx.get('type') != 'transaction':
        raise _format_error('Object type is not "transaction"')
    if 'height' in tx and 'inputs' in tx:
        raise _format_error('Transaction contains both "height" and "inputs"')

    if 'height' in tx:
        _check_keys(tx, {'type', 'height', 'outputs'}, 'Coinbase transaction')
        if not is_non_negative_int(tx['height']):
            raise _format_error('Coinbase height must be a non-negative integer')
    else:
        if 'inputs' not in tx:
            raise _format_error('Non-coinbase transaction has no inputs')
        _check_keys(tx, {'type', 'inputs', 'outputs'}, 'Transaction')
        inputs = tx['inputs']
        if not isinstance(inputs, list):
            raise _format_error('Transaction inputs must be a list')
        if not inputs:
            raise _format_error('Non-coinbase transaction has no inputs')
        for i, inp in enumerate(inputs):
            _check_keys(inp, {'outpoint', 'sig'}, f'Input {i}')
            outpoint = inp['outpoint']
            _check_keys(outpoint, {'txid', 'index'}, f'Outpoint of input {i}')
            if not is_hash(outpoint['txid']):
                raise _format_error(f'Input {i}: txid is not a 32-byte lower-case hex string')
            if not is_non_negative_int(outpoint['index']):
                raise _format_error(f'Input {i}: index must be a non-negative integer')
            if not is_signature(inp['sig']):
                raise _format_error(f'Input {i}: sig is not a 64-byte lower-case hex string')

    outputs = tx['outputs']
    if not isinstance(outputs, list):
        raise _format_error('Transaction outputs must be a list')
    for i, out in enumerate(outputs):
        _check_keys(out, {'pubkey', 'value'}, f'Output {i}')
        if not is_hash(out['pubkey']):
            raise _format_error(f'Output {i}: pubkey is not a 32-byte lower-case hex string')
        if not is_non_negative_int(out['value']):
            raise _format_error(f'Output {i}: value must be a non-negative integer')


def is_coinbase(tx: Dict[str, Any]) -> bool:
    """True if the (syntactically valid) transaction is a coinbase transaction."""
    return 'height' in tx


# ---------- Signatures ----------

def signing_payload(tx: Dict[str, Any]) -> bytes:
    """Return the bytes that are signed: the transaction with all sig values set to null."""
    unsigned = copy.deepcopy(tx)
    for inp in unsigned.get('inputs', []):
        inp['sig'] = None
    return canonical_json(unsigned)


def verify_signature(pubkey_hex: str, sig_hex: str, payload: bytes) -> bool:
    """Verify an Ed25519 signature given as hex strings; False on any failure."""
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
        key.verify(bytes.fromhex(sig_hex), payload)
        return True
    except (InvalidSignature, ValueError):
        return False


# ---------- Semantic validation ----------

def validate_transaction(tx: Any, object_db) -> None:
    """Fully validate a transaction against the object database.

    Raises KermaError with the matching error name:
      INVALID_FORMAT          - malformed transaction, or an outpoint references a non-transaction
      UNKNOWN_OBJECT          - an outpoint references an object not in the database (non-fatal)
      INVALID_TX_OUTPOINT     - the referenced transaction has no output with that index
      INVALID_TX_SIGNATURE    - a signature does not verify
      INVALID_TX_CONSERVATION - an output is spent twice, or outputs exceed inputs
    Syntactically valid coinbase transactions are always considered valid (for now).
    """
    validate_transaction_syntax(tx)
    if is_coinbase(tx):
        return

    # The same outpoint must not be used twice within one transaction.
    seen = set()
    for inp in tx['inputs']:
        key = (inp['outpoint']['txid'], inp['outpoint']['index'])
        if key in seen:
            raise KermaError(
                'INVALID_TX_CONSERVATION',
                f'Transaction spends outpoint {key[0]}:{key[1]} more than once',
            )
        seen.add(key)

    payload = signing_payload(tx)
    input_sum = 0
    for i, inp in enumerate(tx['inputs']):
        txid = inp['outpoint']['txid']
        index = inp['outpoint']['index']

        prev = object_db.get(txid)
        if prev is None:
            raise KermaError('UNKNOWN_OBJECT', f'Input {i} references unknown object {txid}')
        if prev.get('type') != 'transaction':
            raise _format_error(f'Input {i} references {txid}, which is not a transaction')
        if index >= len(prev['outputs']):
            raise KermaError(
                'INVALID_TX_OUTPOINT',
                f'Input {i}: transaction {txid} has no output with index {index}',
            )

        output = prev['outputs'][index]
        if not verify_signature(output['pubkey'], inp['sig'], payload):
            raise KermaError('INVALID_TX_SIGNATURE', f'Input {i}: signature is not valid')
        input_sum += output['value']

    output_sum = sum(out['value'] for out in tx['outputs'])
    if output_sum > input_sum:
        raise KermaError(
            'INVALID_TX_CONSERVATION',
            f'Outputs ({output_sum}) exceed inputs ({input_sum})',
        )
