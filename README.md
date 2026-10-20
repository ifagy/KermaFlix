# Kerma Node – Task 1

Python implementation of a Kerma blockchain node for *Introduction to Blockchain
Technologies* (WS2026/27). This submission covers **Task 1**: the networking core,
the handshake and peer discovery. Blocks, transactions and the mempool are not
part of this task.

## Features

- Listens on TCP port `18018` and supports multiple simultaneous connections
- Canonical JSON encoding of all outgoing messages (sorted keys, compact, `\n`-terminated)
- Message parsing with defragmentation (split packets) and multiple messages per packet
- Handshake: `hello` is followed by `getpeers`; `INVALID_HANDSHAKE` for messages before
  `hello`, a second `hello` or no `hello` within 20 seconds
- `INVALID_FORMAT` for invalid JSON, unknown types, wrong or missing keys, additional keys,
  invalid versions and invalid peer addresses; every error message contains a `msg` explanation
- Peer discovery: hard-coded bootstrap peer, persistent peer list, outbound connections
  to a limited number of known peers
- Valid message types that are not needed yet (e.g. `getchaintip`) are ignored and keep the connection open

## Project layout

```
.
├── README.md
├── auto_test.py          # automated end-to-end tests
├── manual_test.py        # interactive client for manual testing
└── kerma-node/
    ├── Dockerfile
    ├── docker-compose.yml
    ├── .dockerignore
    ├── main.py           # entry point, reads the configuration
    ├── node.py           # TCP server, outbound connections, discovery loop
    ├── connection.py     # handshake, parsing and message handling per connection
    ├── peer_manager.py   # known peers, persistence, peer selection
    └── protocol.py       # message validation and canonical JSON
```

## Requirements

- Docker with Docker Compose
- Python 3.10+ on the host (only needed for the test scripts)

## Configuration

Set in `kerma-node/docker-compose.yml`:

| Variable      | Default                  | Description                                                    |
|---------------|--------------------------|----------------------------------------------------------------|
| `NODE_PORT`   | `18018`                  | TCP port the node listens on                                   |
| `MY_ADDRESS`  | `kermanode.net:18018`    | Public `host:port` advertised first in every `peers` message   |
| `PEERS_FILE`  | `peers.json`             | Location of the persistent peer list (`/data/peers.json` in Docker) |

Set `MY_ADDRESS` to the domain name or static public IPv4 address (plus port) under which
your node is reachable. It must be a valid peer address, otherwise the node does not advertise itself.

## Running the node

Run all commands inside `kerma-node/`:

```bash
docker compose up -d --build   # build and start in the background
docker compose logs -f         # follow the logs
docker compose down            # stop (the peer list is kept in a volume)
docker compose down -v         # stop and delete the peer list
```

## Testing

Start the node first, then run the tests from the project root.

### Automated tests

```bash
python auto_test.py            # fast tests
python auto_test.py --slow     # additionally tests the 20 second handshake timeout
```

The script covers the handshake, defragmentation, error handling, peer validation,
peer persistence, simultaneous connections and ignored message types. It exits with
status 1 if a test fails. Use `KERMA_HOST` and `KERMA_PORT` to test a node on another machine.

The tests add a few non-existent peers (`test-*.example.org`) to the peer list. The node
forgets them automatically; run `docker compose down -v` for a completely clean state.

### Manual testing

```bash
python manual_test.py
```

Type a JSON message and press Enter to send it; type `exit` to quit. Examples:

```json
{"type":"hello","version":"0.10.0","agent":"manual"}
{"type":"getpeers"}
{"type":"hello","version":"5.8.2"}
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
tar -cvf ../submission.tar Dockerfile docker-compose.yml .dockerignore \
    protocol.py peer_manager.py connection.py node.py main.py
tar -tf ../submission.tar    # verify the contents
```

The grader runs `docker-compose build`, `docker-compose up -d` and `docker-compose down -v`
on the extracted archive, so the node always starts with an empty peer list.