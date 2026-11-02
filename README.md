# Kerma Node - Task 2

Python implementation of a Kerma blockchain node for *Introduction to Blockchain
Technologies* (WS2026/27). This submission covers **Task 1** (networking core, handshake,
peer discovery) and **Task 2** (object database, object exchange, gossiping and
transaction validation). Block validation, chains and the mempool are not part of these tasks.

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
  A fresh node contains only the genesis block.
- **Object ids**: `blake2s(canonical JSON)`; verified against the genesis block id
  `00002fa1...57dee` at startup.
- **Object exchange**
  - `ihaveobject` for an unknown object -> the node replies with `getobject`
  - `getobject` -> `object` if known, otherwise `UNKNOWN_OBJECT` (connection stays open)
  - `object` -> already known objects are ignored; new valid transactions are stored
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
  - Syntactically valid coinbase transactions are accepted (as required for this task)
  - Invalid transactions are answered with an error, the connection is closed (outbound
    peers are forgotten) and the transaction is neither stored nor gossiped
- Messages `getobject`/`ihaveobject` require a valid lower-case 64-hex `objectid`;
  `object` requires an `object` dictionary. Objects with a `type` not equal to `transaction`
  give `INVALID_FORMAT` (per the Task 2 specification).

## Project layout

```
.
|-- README.md
|-- auto_test.py            # automated end-to-end tests against the running node
|-- manual_test.py          # interactive client for manual testing
|-- tests/                  # unit and in-process integration tests (no Docker needed)
|   |-- helpers.py              # import path, wallets, transaction builders, example objects
|   |-- test_objects.py         # canonical JSON, object ids, signatures
|   |-- test_transactions.py    # transaction syntax and semantic validation
|   |-- test_object_db.py       # object database and persistence
|   |-- test_protocol_objects.py# getobject/ihaveobject/object message validation
|   `-- test_node_integration.py# real node on localhost: exchange, gossip, validation
`-- kerma-node/
    |-- Dockerfile
    |-- docker-compose.yml
    |-- .dockerignore
    |-- requirements.txt    # cryptography (Ed25519)
    |-- main.py             # entry point, reads the configuration
    |-- node.py             # TCP server, outbound connections, discovery, gossip broadcast
    |-- connection.py       # handshake, parsing and message handling per connection
    |-- peer_manager.py     # known peers, persistence, peer selection
    |-- protocol.py         # message validation and canonical JSON
    |-- objects.py          # object ids, transaction syntax/semantic validation, signatures
    `-- object_db.py        # persistent SQLite object database
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

Set `MY_ADDRESS` to the domain name or static public IPv4 address (plus port) under which
your node is reachable. It must be a valid peer address, otherwise the node does not advertise itself.

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

These start a real node in-process on a random localhost port with temporary storage and
cover every Task 2 feature: object ids, the object database and its persistence across
restarts, message validation, object exchange, gossiping to other peers, and valid and
invalid transactions (including that invalid ones are neither stored nor gossiped).

### Automated end-to-end tests (against the Docker node)

Start the node first, then run from the project root:

```bash
python auto_test.py            # fast tests (Task 1 + Task 2)
python auto_test.py --slow     # additionally tests the 20 second handshake timeout
```

The Task 2 part simulates two graders: sending and requesting objects, gossip to the
second grader, `ihaveobject` -> `getobject`, `UNKNOWN_OBJECT`, the example transactions
from the task description and many kinds of invalid transactions. It exits with status 1
if a test fails. Use `KERMA_HOST` and `KERMA_PORT` to test a node on another machine.

The tests add a few non-existent peers (`test-*.example.org`) and test transactions to the
node. Run `docker compose down -v` for a completely clean state.

### Manual testing

```bash
python manual_test.py
```

Type a JSON message and press Enter to send it; type `exit` to quit. Examples:

```json
{"type":"hello","version":"0.10.0","agent":"manual"}
{"type":"getpeers"}
{"type":"getobject","objectid":"00002fa163c7dab0991544424b9fd302bb1782b185e5a3bbdf12afb758e57dee"}
{"type":"object","object":{"height":0,"outputs":[{"pubkey":"85acb336a150b16a9c6c8c27a4e9c479d9f99060a7945df0bb1b53365e98969b","value":50000000000000}],"type":"transaction"}}
{"type":"ihaveobject","objectid":"d46d09138f0251edc32e28f1a744cb0b7286850e4c9c777d7e3c6e459b289347"}
```

## Peer management

- The bootstrap peer `128.130.122.101:18018` is used when no peers are known.
- Peers learned from `peers` messages are validated and stored in `peers.json`,
  which survives restarts (Docker volume).
- The node keeps at most 8 outbound connections and only connects to a peer
  30 seconds after learning about it.
- A peer is forgotten after 3 failed connection attempts, or immediately if it sends
  invalid data on a connection that this node initiated. Incoming connections never
  cause a peer to be forgotten.
- A `peers` reply contains this node's own address first, followed by up to 29 peers,
  preferring peers without failed attempts and the most recently learned ones.

## Creating the submission archive

The files must be at the root of the archive (no enclosing folder):

```bash
cd kerma-node
tar -cvf ../submission.tar Dockerfile docker-compose.yml .dockerignore requirements.txt \
    protocol.py peer_manager.py connection.py node.py main.py objects.py object_db.py
tar -tf ../submission.tar    # verify the contents
```

The grader runs `docker-compose build`, `docker-compose up -d` and `docker-compose down -v`
on the extracted archive, so the node always starts with an empty peer list and only the
genesis block in its object database. Dependencies are installed during the build, because
the node has no network access while it is graded.
