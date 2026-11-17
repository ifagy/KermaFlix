# Kerma Node - Task 3

Python implementation of a Kerma blockchain node for *Introduction to Blockchain
Technologies* (WS2026/27). This submission covers **Task 1** (networking core, handshake,
peer discovery), **Task 2** (object database, object exchange, gossiping, transaction
validation) and **Task 3** (block validation, missing transaction fetching, coinbase
validation, transaction execution and per-block UTXO sets). Chain tip tracking, recursive
block fetching and the mempool are not part of these tasks.

## Features

### Task 1

- Listens on TCP port `18018` and supports multiple simultaneous connections
- Canonical JSON encoding of all outgoing messages (sorted keys, compact, `\n`-terminated)
- Message parsing with defragmentation (split packets) and multiple messages per packet
- Handshake: `hello` is followed by `getpeers`; `INVALID_HANDSHAKE` for messages before
  `hello`, a second `hello` or no `hello` within 20 seconds
- `INVALID_FORMAT` for invalid JSON, unknown types, wrong or missing keys, additional keys,
  invalid versions and invalid peer addresses; every error message contains a `msg` explanation
- Peer discovery: hard-coded bootstrap peer, persistent peer list, outbound connections
  to a limited number of known peers
- Valid message types that are not needed yet (`getchaintip`, `chaintip`, `getmempool`,
  `mempool`) are ignored and keep the connection open

### Task 2

- **Object database**: persistent SQLite store (`objects.db`, Docker volume) keyed by objectid.
  A fresh node contains only the genesis block (with height `0` and an empty UTXO set).
- **Object ids**: `blake2s(canonical JSON)`; verified against the genesis block id
  `00002fa1...57dee` at startup.
- **Object exchange**
  - `ihaveobject` for an unknown object -> the node replies with `getobject`
  - `getobject` -> `object` if known, otherwise `UNKNOWN_OBJECT` (connection stays open)
  - `object` -> already known objects are ignored; new valid transactions and blocks are stored
- **Gossiping**: every newly stored valid object is announced with `ihaveobject` to all
  connected peers (with completed handshake).
- **Transaction validation**
  - Syntax (`INVALID_FORMAT`): exact key sets for transactions, inputs, outpoints and outputs;
    lower-case hex `txid`/`pubkey` (32 bytes) and `sig` (64 bytes); non-negative integer
    `index`, `value` and `height`; non-coinbase transactions need a non-empty `inputs` list;
    `height` and `inputs` must not both be present
  - Outpoints: unknown `txid` -> `UNKNOWN_OBJECT` (not faulty, connection stays open);
    `txid` refers to a block -> `INVALID_FORMAT`; index out of range -> `INVALID_TX_OUTPOINT`
  - Ed25519 signatures over the transaction with all `sig` set to `null` -> `INVALID_TX_SIGNATURE`
  - The same outpoint used twice, or outputs exceeding inputs -> `INVALID_TX_CONSERVATION`
  - Invalid transactions are answered with an error, the connection is closed (outbound
    peers are forgotten) and the transaction is neither stored nor gossiped

### Task 3

- **Block header validation (`blocks.py`)**
  - Syntax (`INVALID_FORMAT`): required keys `type`, `txids`, `nonce`, `previd`, `created`, `T`,
    optional `miner` and `note` (printable ASCII strings of length `<= 128`), no extra keys
  - Target check: `T` must equal `0000abc000000000000000000000000000000000000000000000000000000000`
    (`INVALID_FORMAT`)
  - Proof-of-Work: `blockid < T` (`INVALID_BLOCK_POW`)
  - Genesis rule: `previd: null` is only allowed for the genesis block (`INVALID_GENESIS`)
  - Parent existence: if `previd` is unknown, the node sends `UNKNOWN_OBJECT` (non-fatal)
  - Timestamps: `parent.created < block.created <= now` (`INVALID_BLOCK_TIMESTAMP`)
- **Missing transaction fetching (`pending.py`, `connection.py`)**
  - If any `txid` in `block.txids` is not in the local database, the node broadcasts
    `getobject` to its connected peers and places block validation on hold for up to `5` seconds
  - If a transaction does not arrive within `5` seconds, the block fails with
    `UNFINDABLE_OBJECT` (non-fatal, connection stays open)
  - If a fetched transaction is invalid, the transaction fails with its specific error and
    the pending block fails with `INVALID_ANCESTRY`
- **Coinbase & UTXO execution (`blocks.py`, `object_db.py`)**
  - Each stored block persists its `(height, utxo_set)` in SQLite (`block_state` table);
    genesis starts at height `0` with `{}`
  - At most one coinbase transaction per block, only at index `0` in `txids`, with no inputs,
    `height == parent.height + 1`, and exactly `1` output of value
    `<= 50 * 10^12 + sum(fees)` (`INVALID_BLOCK_COINBASE`)
  - Non-coinbase transactions are executed in order against the running UTXO set initialized
    from the parent block: every input outpoint must be present in the UTXO set and cannot
    spend the coinbase of the same block (`INVALID_TX_OUTPOINT`), spent outputs are removed,
    and newly created outputs are added (allowing subsequent transactions in the same block
    to spend from earlier non-coinbase transactions)
  - Valid blocks are stored atomically with their resulting UTXO set and gossiped via
    `ihaveobject` to all connected peers

## Project layout

```
.
|-- README.md
|-- auto_test.py            # automated end-to-end tests against the running node
|-- manual_test.py          # interactive client for manual testing
|-- tests/                  # unit and in-process integration tests (no Docker needed)
|   |-- helpers.py              # import path, wallets, transaction/block builders & miner
|   |-- test_objects.py         # canonical JSON, object ids, signatures
|   |-- test_transactions.py    # transaction syntax and semantic validation
|   |-- test_blocks.py          # block syntax, header/PoW, coinbase & UTXO execution
|   |-- test_pending.py         # pending object waiters, timeouts & INVALID_ANCESTRY
|   |-- test_object_db.py       # object database, block UTXO state and persistence
|   |-- test_protocol_objects.py# getobject/ihaveobject/object message validation
|   `-- test_node_integration.py# real node on localhost: exchange, gossip, blocks & UTXO
`-- kerma-node/
    |-- Dockerfile
    |-- docker-compose.yml
    |-- .dockerignore
    |-- requirements.txt    # cryptography (Ed25519)
    |-- main.py             # entry point, reads the configuration
    |-- node.py             # TCP server, outbound connections, discovery, gossip broadcast
    |-- connection.py       # handshake, parsing, object & block handling per connection
    |-- peer_manager.py     # known peers, persistence, peer selection
    |-- protocol.py         # message validation and canonical JSON
    |-- objects.py          # object ids, transaction syntax/semantic validation, signatures
    |-- blocks.py           # block syntax/PoW/header checks and UTXO block execution
    |-- pending.py          # async waiter coordinator for pending block/tx validation
    `-- object_db.py        # persistent SQLite object & per-block UTXO database
```

## Requirements

- Docker with Docker Compose
- Python 3.10+ on the host and `pip install cryptography` (only needed for the tests)

## Configuration

Set in `kerma-node/docker-compose.yml`:

| Variable      | Default                  | Description                                                    |
|---------------|--------------------------|----------------------------------------------------------------|
| `NODE_PORT`   | `18018`                  | TCP port the node listens on                                   |
| `MY_ADDRESS`  | `kermanode.net:18018`    | Public `host:port` advertised first in every `peers` message   |
| `PEERS_FILE`  | `peers.json`             | Location of the persistent peer list (`/data/peers.json` in Docker) |
| `OBJECTS_DB`  | `objects.db`             | Location of the object database (`/data/objects.db` in Docker) |

## Running the node

Run all commands inside `kerma-node/`:

```bash
docker compose up -d --build   # build and start in the background
docker compose logs -f         # follow the logs
docker compose down            # stop (peer list and objects are kept in a volume)
docker compose down -v         # stop and delete peer list and objects
```

## Testing

### Unit and integration tests (no Docker)

```bash
pip install cryptography
python -m unittest discover -s tests -v
```

### Automated end-to-end tests (against the Docker node)

Start the node first, then run from the project root:

```bash
python auto_test.py            # fast tests (Task 1 + Task 2 + Task 3)
python auto_test.py --slow     # additionally tests 5s tx timeout and 20s handshake timeout
```

## Creating the submission archive

The files must be at the root of the archive (no enclosing folder):

```bash
cd kerma-node
tar -cvf ../submission.tar Dockerfile docker-compose.yml .dockerignore requirements.txt \
    protocol.py peer_manager.py connection.py node.py main.py objects.py blocks.py pending.py object_db.py
tar -tf ../submission.tar    # verify the contents
```
