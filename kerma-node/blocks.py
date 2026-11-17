"""Blocks: syntax checks, proof-of-work, and execution of a block's transactions (UTXO set)."""

from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from objects import (
    GENESIS_ID, KermaError, is_coinbase, is_hash, is_non_negative_int,
)

# The fixed mining target of every block.
TARGET = '0000abc000000000000000000000000000000000000000000000000000000000'
# New coins created by a coinbase transaction (picaker).
BLOCK_REWARD = 50 * 10 ** 12
MAX_STR_LEN = 128

REQUIRED_BLOCK_KEYS = {'type', 'txids', 'nonce', 'previd', 'created', 'T'}
OPTIONAL_BLOCK_KEYS = {'miner', 'note'}

# An unspent output: (txid, index).
Outpoint = Tuple[str, int]


def _format_error(msg: str) -> KermaError:
    return KermaError('INVALID_FORMAT', msg)


def _is_printable_ascii_str(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_STR_LEN
        and all(0x20 <= ord(c) <= 0x7E for c in value)
    )


# ---------- Header checks ----------

def validate_block_syntax(block: Any) -> None:
    """Check keys and value formats of a block; raise INVALID_FORMAT on errors."""
    if not isinstance(block, dict):
        raise _format_error('Block is not a JSON object')
    if block.get('type') != 'block':
        raise _format_error('Object type is not "block"')
    missing = REQUIRED_BLOCK_KEYS - set(block)
    if missing:
        raise _format_error(f'Block is missing keys: {sorted(missing)}')
    extra = set(block) - REQUIRED_BLOCK_KEYS - OPTIONAL_BLOCK_KEYS
    if extra:
        raise _format_error(f'Block has additional keys: {sorted(extra)}')

    txids = block['txids']
    if not isinstance(txids, list) or not all(is_hash(t) for t in txids):
        raise _format_error('txids must be a list of 32-byte lower-case hex strings')
    if not is_hash(block['nonce']):
        raise _format_error('nonce must be a 32-byte lower-case hex string')
    if block['previd'] is not None and not is_hash(block['previd']):
        raise _format_error('previd must be null or a 32-byte lower-case hex string')
    if not is_non_negative_int(block['created']):
        raise _format_error('created must be a non-negative integer UNIX timestamp')
    if not is_hash(block['T']):
        raise _format_error('T must be a 32-byte lower-case hex string')
    for key in OPTIONAL_BLOCK_KEYS & set(block):
        if not _is_printable_ascii_str(block[key]):
            raise _format_error(f'{key} must be a printable ASCII string of at most 128 characters')


def has_valid_pow(blockid: str) -> bool:
    """True if the block id is below the mining target."""
    return int(blockid, 16) < int(TARGET, 16)


def validate_block_header(block: Any, blockid: str) -> None:
    """Checks that need no other objects: syntax, target, proof-of-work and genesis.

    Raises INVALID_FORMAT, INVALID_BLOCK_POW or INVALID_GENESIS.
    """
    validate_block_syntax(block)
    if block['T'] != TARGET:
        raise _format_error(f'Block has target {block["T"]}, expected {TARGET}')
    if not has_valid_pow(blockid):
        raise KermaError('INVALID_BLOCK_POW', f'Block {blockid} does not meet the mining target')
    if block['previd'] is None and blockid != GENESIS_ID:
        raise KermaError('INVALID_GENESIS', f'Block {blockid} has previd null but is not the genesis block')


# ---------- Execution ----------

def execute_block(
        block: Dict[str, Any],
        parent: Dict[str, Any],
        parent_height: int,
        parent_utxo: Set[Outpoint],
        txs: List[Any],
        get_tx: Callable[[str], Optional[Dict[str, Any]]],
        now: float,
) -> Tuple[int, Set[Outpoint]]:
    """Validate a block against its parent and execute its transactions.

    txs are the objects referenced by block['txids'] (same order); they are
    transactions that already passed the stand-alone validation of Task 2.
    get_tx looks up any stored transaction by id (used for input values).

    Returns (height, utxo set after the block). Raises KermaError:
      INVALID_FORMAT          - parent is not a block, or a txid refers to a non-transaction
      INVALID_BLOCK_TIMESTAMP - created not after the parent, or in the future
      INVALID_BLOCK_COINBASE  - coinbase misplaced/duplicated/wrong height/too many coins
      INVALID_TX_OUTPOINT     - an input is not in the UTXO set (missing, spent, or the
                                coinbase of the same block)
    """
    if not isinstance(parent, dict) or parent.get('type') != 'block':
        raise _format_error(f'previd {block["previd"]} does not refer to a block')
    if block['created'] <= parent['created']:
        raise KermaError(
            'INVALID_BLOCK_TIMESTAMP',
            f'created {block["created"]} is not later than the parent ({parent["created"]})',
        )
    if block['created'] > now:
        raise KermaError('INVALID_BLOCK_TIMESTAMP', f'created {block["created"]} is in the future')

    txids = block['txids']
    for txid, tx in zip(txids, txs):
        if not isinstance(tx, dict) or tx.get('type') != 'transaction':
            raise _format_error(f'txid {txid} does not refer to a transaction')

    height = parent_height + 1

    # Coinbase rules.
    coinbase_positions = [i for i, tx in enumerate(txs) if is_coinbase(tx)]
    if len(coinbase_positions) > 1:
        raise KermaError('INVALID_BLOCK_COINBASE', 'Block contains more than one coinbase transaction')
    coinbase = None
    coinbase_id = None
    if coinbase_positions:
        if coinbase_positions[0] != 0:
            raise KermaError('INVALID_BLOCK_COINBASE', 'Coinbase transaction is not at index 0')
        coinbase, coinbase_id = txs[0], txids[0]
        if len(coinbase['outputs']) != 1:
            raise KermaError('INVALID_BLOCK_COINBASE', 'Coinbase transaction must have exactly one output')
        if coinbase['height'] != height:
            raise KermaError(
                'INVALID_BLOCK_COINBASE',
                f'Coinbase height {coinbase["height"]} does not match block height {height}',
            )

    # Execute the non-coinbase transactions in order.
    utxo = set(parent_utxo)
    fees = 0
    for txid, tx in zip(txids, txs):
        if tx is coinbase:
            continue
        input_sum = 0
        for inp in tx['inputs']:
            outpoint = (inp['outpoint']['txid'], inp['outpoint']['index'])
            if outpoint[0] == coinbase_id:
                raise KermaError(
                    'INVALID_TX_OUTPOINT',
                    f'Transaction {txid} spends the coinbase of the same block',
                )
            if outpoint not in utxo:
                raise KermaError(
                    'INVALID_TX_OUTPOINT',
                    f'Transaction {txid} spends {outpoint[0]}:{outpoint[1]}, which is not in the UTXO set',
                )
            utxo.remove(outpoint)
            input_sum += get_tx(outpoint[0])['outputs'][outpoint[1]]['value']
        for index in range(len(tx['outputs'])):
            utxo.add((txid, index))
        fees += input_sum - sum(out['value'] for out in tx['outputs'])

    if coinbase is not None:
        value = coinbase['outputs'][0]['value']
        if value > BLOCK_REWARD + fees:
            raise KermaError(
                'INVALID_BLOCK_COINBASE',
                f'Coinbase output {value} exceeds block reward plus fees ({BLOCK_REWARD + fees})',
            )
        utxo.add((coinbase_id, 0))

    return height, utxo
