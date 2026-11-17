"""End-to-end tests for the Kerma node (Task 1 and Task 2).

Start the node first, e.g.:

    cd kerma-node && docker compose up -d --build

Then run the tests from the project root:


    python auto_test.py            # all fast tests
    python auto_test.py --slow     # additionally runs the 20 s handshake timeout test

The Task 2 tests sign transactions and need the 'cryptography' package on the host
(pip install cryptography).
The target can be changed with the environment variables KERMA_HOST and KERMA_PORT
(default 127.0.0.1:18018). The script exits with status 1 if any test fails.
"""

import json
import os
import random
import re
import socket
import string
import sys
import time

HOST = os.environ.get('KERMA_HOST', '127.0.0.1')
PORT = int(os.environ.get('KERMA_PORT', 18018))

HELLO = b'{"type":"hello","version":"0.10.0","agent":"auto-test"}\n'
GETPEERS = b'{"type":"getpeers"}\n'
VERSION_RE = re.compile(r'0\.10\.[0-9]')
PEER_RE = re.compile(r'[^:\s]+:[0-9]{1,5}')

# Invalid messages sent as the very first message (expected: INVALID_FORMAT).
INVALID_BEFORE_HELLO = [
    b'Wbgygvf7rgtyv7tfbgy{{{\n',
    b'{"type": "diufygeuybhv"}\n',
    b'{"type": "hello"}\n',
    b'{"type": "hello", "version":"jd3.x"}\n',
    b'{"type": "hello", "version":"5.8.2"}\n',
    b'{"type":"hello","version":"0.10.12"}\n',
    b'{"type":"hello","version":10}\n',
    b'{"type":"hello","version":"0.10.0","agent":"' + b'a' * 129 + b'"}\n',
    b'{"type":"hello","version":"0.10.0","agent":"bad\\u0001agent"}\n',
    b'{"type":"hello","version":"0.10.0","agent":"x","extra":1}\n',
    b'[1,2,3]\n',
    b'\xff\xfe\n',
    ]

# Valid message types sent before hello (expected: INVALID_HANDSHAKE).
VALID_BEFORE_HELLO = [
    b'{"type":"getpeers"}\n',
    b'{"type":"peers","peers":[]}\n',
    b'{"type":"getchaintip"}\n',
]

# Invalid messages sent after a completed handshake (expected: INVALID_FORMAT).
INVALID_AFTER_HELLO = [
    b'not json {{{\n',
    b'{"type":"unknowntype"}\n',
    b'[1,2,3]\n',
    b'\xff\xfe\n',
    b'{"type":"getpeers","extra":1}\n',
    b'{"type":"peers"}\n',
    b'{"type":"peers","peers":"nope"}\n',
    b'{"type":"peers","peers":[],"extra":1}\n',
]

# Syntactically invalid peer entries (expected: INVALID_FORMAT).
INVALID_PEERS = [
    '256.2.3.4:18018',
    '1.2.3.4.5:678',
    '1.2.3.4:2000000',
    '192.168.0.2:18018',
    'nodotindomain:1234',
    'kermanode.net',
    '1.2.3.4:0',
    '1.2.3.4:65536',
    '127.0.0.1:18018',
    '10.0.0.1:18018',
    'exa mple.com:18018',
    12345,
]

_open_clients = []


class ConnectionClosed(Exception):
    """Raised when the node closed the connection."""


class Client:
    """Minimal line-based TCP client."""

    def __init__(self):
        self.sock = socket.create_connection((HOST, PORT), timeout=5)
        self.buf = b''
        _open_clients.append(self)

    def send(self, data):
        self.sock.sendall(data)

    def send_json(self, obj):
        self.send(json.dumps(obj).encode('utf-8') + b'\n')

    def read_line(self, timeout=3.0):
        """Return the next line (without newline) or raise TimeoutError/ConnectionClosed."""
        deadline = time.time() + timeout
        while b'\n' not in self.buf:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError('timed out waiting for a message')
            self.sock.settimeout(remaining)
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                raise TimeoutError('timed out waiting for a message')
            except (ConnectionResetError, BrokenPipeError):
                raise ConnectionClosed()
            if not data:
                raise ConnectionClosed()
            self.buf += data
        line, self.buf = self.buf.split(b'\n', 1)
        return line

    def read_msg(self, timeout=3.0):
        """Return the next message parsed as JSON."""
        return json.loads(self.read_line(timeout))

    def expect_closed(self, timeout=3.0):
        """Assert that the node closes the connection."""
        try:
            line = self.read_line(timeout)
        except ConnectionClosed:
            return
        except TimeoutError:
            raise AssertionError('connection was not closed')
        raise AssertionError(f'expected the connection to close, got {line!r}')

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def check(condition, message):
    """Raise an AssertionError with the given message if condition is false."""
    if not condition:
        raise AssertionError(message)


def canonical(obj):
    """Canonical JSON encoding used by the protocol (without newline)."""
    return json.dumps(
        obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    ).encode('utf-8')


def fake_peer():
    """Return a syntactically valid peer address that does not exist."""
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f'test-{suffix}.example.org:18018'


def connect():
    """Connect and verify the initial hello and getpeers sent by the node."""
    c = Client()
    raw = c.read_line()
    hello = json.loads(raw)
    check(hello.get('type') == 'hello', f'first message must be hello, got {hello}')
    check(
        isinstance(hello.get('version'), str) and VERSION_RE.fullmatch(hello['version']),
        f'invalid version in hello: {hello}',
        )
    check(set(hello) <= {'type', 'version', 'agent'}, f'unexpected keys in hello: {hello}')
    if 'agent' in hello:
        agent = hello['agent']
        check(
            isinstance(agent, str)
            and len(agent) <= 128
            and all(0x20 <= ord(ch) <= 0x7E for ch in agent),
            f'invalid agent in hello: {hello}',
            )
    check(canonical(hello) == raw, f'hello is not canonical JSON: {raw!r}')
    second = c.read_msg()
    check(second == {'type': 'getpeers'}, f'hello must be followed by getpeers, got {second}')
    return c


def handshake():
    """Connect and complete the handshake."""
    c = connect()
    c.send(HELLO)
    return c


def read_peers(c):
    """Read a peers message, validate its structure and return the peer list."""
    msg = c.read_msg()
    check(msg.get('type') == 'peers', f'expected peers message, got {msg}')
    check(set(msg) == {'type', 'peers'}, f'unexpected keys in peers message: {msg}')
    peers = msg['peers']
    check(isinstance(peers, list) and len(peers) <= 30, f'invalid peers list: {peers}')
    for p in peers:
        check(isinstance(p, str) and PEER_RE.fullmatch(p), f'invalid peer entry: {p!r}')
    check(len(set(peers)) == len(peers), f'duplicate peers: {peers}')
    return peers


def request_peers(c):
    """Send getpeers and return the peers from the reply."""
    c.send(GETPEERS)
    return read_peers(c)


def expect_error(c, name):
    """Assert that the node sends the given error (with a msg) and closes the connection."""
    msg = c.read_msg()
    check(msg.get('type') == 'error', f'expected error message, got {msg}')
    check(msg.get('name') == name, f'expected {name}, got {msg}')
    check(isinstance(msg.get('msg'), str) and msg['msg'], f'error has no msg key: {msg}')
    c.expect_closed()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_handshake_and_getpeers():
    """Node sends hello + getpeers and answers getpeers with a valid peers message."""
    c = handshake()
    request_peers(c)


def test_hello_without_agent_is_accepted():
    """A hello without the optional agent key is accepted."""
    c = connect()
    c.send(b'{"type":"hello","version":"0.10.0"}\n')
    request_peers(c)


def test_non_canonical_json_is_accepted():
    """Valid JSON with extra whitespace and different key order is accepted."""
    c = connect()
    c.send(b'{ "version" : "0.10.0" , "type" : "hello" }\n')
    c.send(b'{  "type" :  "getpeers"  }\n')
    read_peers(c)


def test_defragmentation():
    """A message split over several packets is reassembled."""
    c = handshake()
    c.send(b'{"type":"ge')
    time.sleep(0.1)
    c.send(b'tpeers"}\n')
    read_peers(c)

    for byte in GETPEERS:  # one byte per packet
        c.send(bytes([byte]))
        time.sleep(0.005)
    read_peers(c)


def test_multiple_messages_in_one_packet():
    """Several newline-separated messages in a single packet are all processed."""
    c = connect()
    c.send(HELLO + GETPEERS + GETPEERS)
    read_peers(c)
    read_peers(c)


def test_message_before_hello():
    """Any valid message before hello gives INVALID_HANDSHAKE and closes the connection."""
    for payload in VALID_BEFORE_HELLO:
        c = connect()
        c.send(payload)
        expect_error(c, 'INVALID_HANDSHAKE')


def test_second_hello():
    """A second hello gives INVALID_HANDSHAKE and closes the connection."""
    c = connect()
    c.send(HELLO)
    c.send(HELLO)
    expect_error(c, 'INVALID_HANDSHAKE')


def test_invalid_messages_before_hello():
    """Malformed first messages give INVALID_FORMAT and close the connection."""
    for payload in INVALID_BEFORE_HELLO:
        c = connect()
        c.send(payload)
        try:
            expect_error(c, 'INVALID_FORMAT')
        except AssertionError as e:
            raise AssertionError(f'payload {payload[:60]!r}: {e}')


def test_invalid_messages_after_hello():
    """Malformed messages after the handshake give INVALID_FORMAT and close the connection."""
    for payload in INVALID_AFTER_HELLO:
        c = handshake()
        c.send(payload)
        try:
            expect_error(c, 'INVALID_FORMAT')
        except AssertionError as e:
            raise AssertionError(f'payload {payload[:60]!r}: {e}')


def test_invalid_peer_addresses():
    """A peers message with an invalid address gives INVALID_FORMAT."""
    for bad in INVALID_PEERS:
        c = handshake()
        c.send_json({'type': 'peers', 'peers': [bad]})
        try:
            expect_error(c, 'INVALID_FORMAT')
        except AssertionError as e:
            raise AssertionError(f'peer {bad!r}: {e}')

    # One invalid entry among valid ones is enough to reject the whole message.
    c = handshake()
    c.send_json({'type': 'peers', 'peers': [fake_peer(), '192.168.0.2:18018']})
    expect_error(c, 'INVALID_FORMAT')


def test_too_many_peers():
    """A peers message with more than 30 entries gives INVALID_FORMAT."""
    c = handshake()
    c.send_json({'type': 'peers', 'peers': [fake_peer() for _ in range(31)]})
    expect_error(c, 'INVALID_FORMAT')


def test_valid_peers_messages_keep_connection_open():
    """Empty and valid peers messages are accepted without closing the connection."""
    c = handshake()
    c.send_json({'type': 'peers', 'peers': []})
    c.send_json({'type': 'peers', 'peers': [fake_peer()]})
    request_peers(c)


def test_peer_persistence():
    """Peers sent in one connection are returned in a later connection."""
    peers = [fake_peer(), '138.197.191.170:18018']
    c1 = handshake()
    c1.send_json({'type': 'peers', 'peers': peers})
    time.sleep(0.2)
    c1.close()

    c2 = handshake()
    received = request_peers(c2)
    for p in peers:
        check(p in received, f'peer {p} missing from reply: {received}')


def test_peers_reply_is_limited_to_30():
    """The node never sends more than 30 peers, even if it knows many more."""
    c = handshake()
    for _ in range(2):
        c.send_json({'type': 'peers', 'peers': [fake_peer() for _ in range(30)]})
    time.sleep(0.2)
    peers = request_peers(c)  # read_peers checks the limit
    check(len(peers) == 30, f'expected a full list of 30 peers, got {len(peers)}')


def test_simultaneous_connections():
    """Several clients can be connected at the same time and are handled independently."""
    a = handshake()
    b = handshake()
    request_peers(a)
    request_peers(b)
    a.close()
    request_peers(b)  # b must be unaffected by a closing


def test_reconnect():
    """A client can disconnect and connect again."""
    c = handshake()
    request_peers(c)
    c.close()
    c = handshake()
    request_peers(c)


def test_ignored_valid_messages():
    """Valid message types that are not handled yet are ignored without closing the connection."""
    c = handshake()
    c.send(b'{"type":"getchaintip"}\n')
    c.send(b'{"type":"getmempool"}\n')
    c.send(b'{"type":"chaintip","blockid":"00"}\n')
    c.send(b'{"type":"error","name":"INVALID_FORMAT","msg":"test"}\n')
    request_peers(c)


def test_idle_connection_stays_open():
    """The connection is kept alive after the handshake."""
    c = handshake()
    time.sleep(2)
    request_peers(c)


def test_node_still_alive():
    """After all tests the node still accepts and serves new connections."""
    c = handshake()
    request_peers(c)


def test_handshake_timeout():
    """Without a hello the node sends INVALID_HANDSHAKE after 20 seconds (slow)."""
    c = connect()
    msg = c.read_msg(timeout=25)
    check(
        msg.get('type') == 'error' and msg.get('name') == 'INVALID_HANDSHAKE',
        f'expected INVALID_HANDSHAKE after 20 s, got {msg}',
        )
    c.expect_closed()


# ---------------------------------------------------------------------------
# Task 2: object exchange, gossiping and transaction validation
# ---------------------------------------------------------------------------

GENESIS_ID = '00002fa163c7dab0991544424b9fd302bb1782b185e5a3bbdf12afb758e57dee'
EXAMPLE_COINBASE = {
    'height': 0,
    'outputs': [{
        'pubkey': '85acb336a150b16a9c6c8c27a4e9c479d9f99060a7945df0bb1b53365e98969b',
        'value': 50000000000000,
    }],
    'type': 'transaction',
}
EXAMPLE_COINBASE_ID = 'd46d09138f0251edc32e28f1a744cb0b7286850e4c9c777d7e3c6e459b289347'
EXAMPLE_TX = {
    'inputs': [{
        'outpoint': {'index': 0, 'txid': EXAMPLE_COINBASE_ID},
        'sig': '6204bbab1b736ce2133c4ea43aff3767c49c881ac80b57ba38a3bab980466644'
               'cdbacc86b1f4357cfe45e6374b963f5455f26df0a86338310df33e50c15d7f04',
    }],
    'outputs': [
        {'pubkey': 'b539258e808b3e3354b9776d1ff4146b52282e864f56224e7e33e7932ec72985', 'value': 10},
        {'pubkey': '8dbcd2401c89c04d6e53c81c90aa0b551cc8fc47c0469217c8f5cfbae1e911f9',
         'value': 49999999999990},
    ],
    'type': 'transaction',
}
EXAMPLE_TX_ID = '895ca2bea390b7508f780c7174900a631e73905dcdc6c07a6b61ede2ebd4033f'


def object_id(obj):
    """blake2s hash of the canonical JSON of obj."""
    import hashlib
    return hashlib.blake2s(canonical(obj)).hexdigest()


class Wallet:
    """An Ed25519 key pair (requires the 'cryptography' package)."""

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        self.key = Ed25519PrivateKey.generate()
        self.pubkey = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def new_coinbase(wallet, value=100):
    """A coinbase transaction that is new to the node (random height)."""
    return {
        'type': 'transaction',
        'height': random.randint(0, 10 ** 9),
        'outputs': [{'pubkey': wallet.pubkey, 'value': value}],
    }


def signed_tx(inputs, outputs):
    """inputs: list of (txid, index, wallet); outputs: list of (pubkey, value)."""
    tx = {
        'type': 'transaction',
        'inputs': [{'outpoint': {'txid': t, 'index': i}, 'sig': None} for t, i, _ in inputs],
        'outputs': [{'pubkey': p, 'value': v} for p, v in outputs],
    }
    payload = canonical(tx)
    for inp, (_, _, wallet) in zip(tx['inputs'], inputs):
        inp['sig'] = wallet.key.sign(payload).hex()
    return tx


def read_type(c, msg_type, timeout=3.0):
    """Read messages until one of the given type arrives."""
    deadline = time.time() + timeout
    while True:
        msg = c.read_msg(max(0.01, deadline - time.time()))
        if msg.get('type') == msg_type:
            return msg


def expect_ihave(c, oid, timeout=3.0):
    """Assert that an ihaveobject for oid arrives."""
    deadline = time.time() + timeout
    while True:
        msg = read_type(c, 'ihaveobject', max(0.01, deadline - time.time()))
        if msg.get('objectid') == oid:
            return


def expect_no_ihave(c, oid, timeout=1.0):
    """Assert that no ihaveobject for oid arrives within timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msg = c.read_msg(max(0.01, deadline - time.time()))
        except TimeoutError:
            return
        check(
            not (msg.get('type') == 'ihaveobject' and msg.get('objectid') == oid),
            f'invalid object {oid} was gossiped',
        )


def send_object(c, obj):
    c.send_json({'type': 'object', 'object': obj})


def get_object(c, oid):
    """Request oid and return the object."""
    c.send_json({'type': 'getobject', 'objectid': oid})
    msg = read_type(c, 'object')
    check(set(msg) == {'type', 'object'}, f'unexpected keys in object message: {msg}')
    return msg['object']


def stored_coinbase(c):
    """Send a new coinbase via c, wait until it is stored and return (wallet, cb, id)."""
    w = Wallet()
    cb = new_coinbase(w)
    send_object(c, cb)
    oid = object_id(cb)
    check(get_object(c, oid) == cb, 'coinbase was not stored')
    return w, cb, oid


def test_genesis_block_available():
    """The genesis block is in the object database from the start."""
    c = handshake()
    obj = get_object(c, GENESIS_ID)
    check(object_id(obj) == GENESIS_ID, f'wrong genesis block: {obj}')


def test_example_transactions_accepted():
    """The example transactions from the task description are accepted and stored."""
    c = handshake()
    send_object(c, EXAMPLE_COINBASE)
    check(get_object(c, EXAMPLE_COINBASE_ID) == EXAMPLE_COINBASE, 'example coinbase not stored')
    send_object(c, EXAMPLE_TX)
    check(get_object(c, EXAMPLE_TX_ID) == EXAMPLE_TX, 'example transaction not stored')


def test_object_send_and_request_same_grader():
    """Grader 1 sends a new valid transaction and can request it back."""
    c = handshake()
    stored_coinbase(c)


def test_object_send_and_request_other_grader():
    """Grader 1 sends a new valid transaction, Grader 2 can request it."""
    g1, g2 = handshake(), handshake()
    request_peers(g2)
    cb = new_coinbase(Wallet())
    send_object(g1, cb)
    expect_ihave(g2, object_id(cb))
    check(get_object(g2, object_id(cb)) == cb, 'Grader 2 did not receive the object')


def test_valid_transaction_gossiped():
    """A new valid (signed) transaction from Grader 1 is announced to Grader 2."""
    g1, g2 = handshake(), handshake()
    request_peers(g2)
    w, cb, cb_id = stored_coinbase(g1)
    expect_ihave(g2, cb_id)
    tx = signed_tx([(cb_id, 0, w)], [(Wallet().pubkey, 60), (w.pubkey, 40)])
    send_object(g1, tx)
    expect_ihave(g2, object_id(tx))


def test_ihaveobject_triggers_getobject():
    """ihaveobject with an unknown id is answered with getobject for that id."""
    c = handshake()
    oid = object_id({'random': random.random()})
    c.send_json({'type': 'ihaveobject', 'objectid': oid})
    msg = read_type(c, 'getobject')
    check(msg == {'type': 'getobject', 'objectid': oid}, f'unexpected getobject: {msg}')


def test_ihaveobject_known_object_not_requested():
    """ihaveobject with a known id is not answered with getobject."""
    c = handshake()
    c.send_json({'type': 'ihaveobject', 'objectid': GENESIS_ID})
    c.send(GETPEERS)
    msg = c.read_msg()  # the next message must be the peers reply, not a getobject
    check(msg.get('type') == 'peers', f'expected only a peers reply, got {msg}')


def test_getobject_unknown():
    """getobject for an unknown id gives UNKNOWN_OBJECT and keeps the connection open."""
    c = handshake()
    c.send_json({'type': 'getobject', 'objectid': 'ee' * 32})
    msg = read_type(c, 'error')
    check(msg.get('name') == 'UNKNOWN_OBJECT', f'expected UNKNOWN_OBJECT, got {msg}')
    request_peers(c)


def test_invalid_object_messages():
    """Malformed getobject/ihaveobject/object messages give INVALID_FORMAT."""
    payloads = [
        {'type': 'getobject'},
        {'type': 'getobject', 'objectid': 'XYZ'},
        {'type': 'getobject', 'objectid': 'AB' * 32},
        {'type': 'ihaveobject', 'objectid': 12},
        {'type': 'ihaveobject', 'objectid': GENESIS_ID, 'extra': 1},
        {'type': 'object'},
        {'type': 'object', 'object': 'nope'},
        {'type': 'object', 'object': {'type': 'banana'}},
    ]
    for p in payloads:
        c = handshake()
        c.send_json(p)
        try:
            expect_error(c, 'INVALID_FORMAT')
        except AssertionError as e:
            raise AssertionError(f'payload {p}: {e}')


def _invalid_tx_cases():
    """Return ([(description, tx, expected_error)], coinbase that must be stored first)."""
    w, other = Wallet(), Wallet()
    cb = new_coinbase(w, value=100)
    cb_id = object_id(cb)
    tampered = signed_tx([(cb_id, 0, w)], [(other.pubkey, 10)])
    tampered['outputs'][0]['value'] = 11
    return [
        ('wrong signer', signed_tx([(cb_id, 0, other)], [(other.pubkey, 10)]), 'INVALID_TX_SIGNATURE'),
        ('tampered output', tampered, 'INVALID_TX_SIGNATURE'),
        ('outputs exceed inputs', signed_tx([(cb_id, 0, w)], [(other.pubkey, 101)]),
         'INVALID_TX_CONSERVATION'),
        ('same outpoint twice', signed_tx([(cb_id, 0, w)] * 2, [(other.pubkey, 1)]),
         'INVALID_TX_CONSERVATION'),
        ('index too high', signed_tx([(cb_id, 1, w)], [(other.pubkey, 1)]), 'INVALID_TX_OUTPOINT'),
        ('outpoint is a block', signed_tx([(GENESIS_ID, 0, w)], [(other.pubkey, 1)]),
         'INVALID_FORMAT'),
        ('negative value', {'type': 'transaction', 'height': 1,
                            'outputs': [{'pubkey': w.pubkey, 'value': -1}]}, 'INVALID_FORMAT'),
        ('float value', {'type': 'transaction', 'height': 1,
                         'outputs': [{'pubkey': w.pubkey, 'value': 1.5}]}, 'INVALID_FORMAT'),
        ('invalid pubkey', {'type': 'transaction', 'height': 1,
                            'outputs': [{'pubkey': 'xyz', 'value': 1}]}, 'INVALID_FORMAT'),
        ('no inputs', {'type': 'transaction', 'inputs': [], 'outputs': []}, 'INVALID_FORMAT'),
        ('missing inputs', {'type': 'transaction', 'outputs': []}, 'INVALID_FORMAT'),
        ('height and inputs', dict(signed_tx([(cb_id, 0, w)], []), height=1), 'INVALID_FORMAT'),
        ('additional key', dict(cb, extra=1), 'INVALID_FORMAT'),
        ('invalid sig format', {'type': 'transaction', 'outputs': [], 'inputs': [
            {'outpoint': {'txid': cb_id, 'index': 0}, 'sig': 'abc'}]}, 'INVALID_FORMAT'),
    ], cb


def test_invalid_transactions_rejected_and_not_gossiped():
    """Invalid transactions give the right error, close the connection and are not gossiped."""
    cases, cb = _invalid_tx_cases()
    setup = handshake()
    send_object(setup, cb)
    get_object(setup, object_id(cb))
    for desc, tx, error in cases:
        g1, g2 = handshake(), handshake()
        request_peers(g2)
        send_object(g1, tx)
        try:
            expect_error(g1, error)
            expect_no_ihave(g2, object_id(tx))
            g2.send_json({'type': 'getobject', 'objectid': object_id(tx)})
            msg = read_type(g2, 'error')
            check(msg.get('name') == 'UNKNOWN_OBJECT', f'invalid tx was stored: {msg}')
        except AssertionError as e:
            raise AssertionError(f'{desc}: {e}')
        g1.close()
        g2.close()


def test_unknown_outpoint_keeps_connection():
    """A transaction spending an unknown object gives UNKNOWN_OBJECT without closing."""
    g1, g2 = handshake(), handshake()
    request_peers(g2)
    w = Wallet()
    tx = signed_tx([('ee' * 32, 0, w)], [(w.pubkey, 1)])
    send_object(g1, tx)
    msg = read_type(g1, 'error')
    check(msg.get('name') == 'UNKNOWN_OBJECT', f'expected UNKNOWN_OBJECT, got {msg}')
    request_peers(g1)
    expect_no_ihave(g2, object_id(tx))


def test_known_object_not_gossiped_again():
    """Sending an already known object again does not trigger gossip."""
    g1, g2 = handshake(), handshake()
    request_peers(g2)
    _, cb, cb_id = stored_coinbase(g1)
    expect_ihave(g2, cb_id)
    send_object(g1, cb)
    expect_no_ihave(g2, cb_id)
    request_peers(g1)


TASK2_TESTS = [
    test_genesis_block_available,
    test_example_transactions_accepted,
    test_object_send_and_request_same_grader,
    test_object_send_and_request_other_grader,
    test_valid_transaction_gossiped,
    test_ihaveobject_triggers_getobject,
    test_ihaveobject_known_object_not_requested,
    test_getobject_unknown,
    test_invalid_object_messages,
    test_invalid_transactions_rejected_and_not_gossiped,
    test_unknown_outpoint_keeps_connection,
    test_known_object_not_gossiped_again,
]


# ---------------------------------------------------------------------------
# Task 3: block validation, transaction fetching and UTXO execution
# ---------------------------------------------------------------------------

TARGET = '0000abc000000000000000000000000000000000000000000000000000000000'
GENESIS_CREATED = 1671062400
BLOCK_REWARD = 50 * 10 ** 12

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

_NONCE_MARKER = 'n' * 64


def mine_block(block):
    """Return a copy of block with a valid proof-of-work nonce."""
    import hashlib
    template = canonical(dict(block, nonce=_NONCE_MARKER))
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


def unmine_block(block):
    """Return a copy of block with an invalid proof-of-work nonce."""
    import hashlib
    target = bytes.fromhex(TARGET)
    n = 0
    while True:
        cand = dict(block, nonce='%064x' % n)
        if hashlib.blake2s(canonical(cand)).digest() >= target:
            return cand
        n += 1


def make_block(txids=(), previd=GENESIS_ID, created=None, do_mine=True, **extra):
    """Build (and optionally mine) a block."""
    block = {
        'type': 'block',
        'txids': list(txids),
        'nonce': '0' * 64,
        'previd': previd,
        'created': GENESIS_CREATED + 1 if created is None else created,
        'T': TARGET,
        'miner': 'auto-test',
        'note': ''.join(random.choices(string.ascii_lowercase, k=12)),
    }
    block.update(extra)
    return mine_block(block) if do_mine else block


def test_task3_example_block_accepted():
    """The example coinbase and block from the Task 3 description are accepted."""
    c = handshake()
    send_object(c, EXAMPLE_CB_H1)
    check(get_object(c, EXAMPLE_CB_H1_ID) == EXAMPLE_CB_H1, 'Task 3 example coinbase not stored')
    send_object(c, EXAMPLE_BLOCK)
    check(get_object(c, EXAMPLE_BLOCK_ID) == EXAMPLE_BLOCK, 'Task 3 example block not stored')


def test_valid_block_gossiped():
    """A new valid block sent by Grader 1 is gossiped to Grader 2."""
    g1, g2 = handshake(), handshake()
    request_peers(g2)
    w = Wallet()
    cb = {'type': 'transaction', 'height': 1, 'outputs': [{'pubkey': w.pubkey, 'value': BLOCK_REWARD}]}
    send_object(g1, cb)
    expect_ihave(g2, object_id(cb))
    b1 = make_block([object_id(cb)])
    send_object(g1, b1)
    expect_ihave(g2, object_id(b1))
    check(get_object(g2, object_id(b1)) == b1, 'Grader 2 could not fetch the stored block')


def test_block_fetches_missing_transaction():
    """When a block references an unknown tx, the node requests it via getobject and validates."""
    g1, g2 = handshake(), handshake()
    request_peers(g2)
    w = Wallet()
    cb = {'type': 'transaction', 'height': 1, 'outputs': [{'pubkey': w.pubkey, 'value': BLOCK_REWARD}]}
    cb_id = object_id(cb)
    b1 = make_block([cb_id])
    b1_id = object_id(b1)

    send_object(g1, b1)
    req = read_type(g1, 'getobject')
    check(req == {'type': 'getobject', 'objectid': cb_id}, f'expected getobject for {cb_id}, got {req}')
    send_object(g1, cb)

    expect_ihave(g2, b1_id)
    check(get_object(g1, b1_id) == b1, 'block was not stored after tx arrived')


def test_block_with_invalid_fetched_tx_gives_invalid_ancestry():
    """If a fetched transaction in a block turns out to be invalid, INVALID_ANCESTRY is sent."""
    g1, g2 = handshake(), handshake()
    request_peers(g2)
    alice, bob = Wallet(), Wallet()
    cb = {'type': 'transaction', 'height': 1, 'outputs': [{'pubkey': alice.pubkey, 'value': 100}]}
    cb_id = object_id(cb)
    b1 = make_block([cb_id])
    b1_id = object_id(b1)
    send_object(g1, cb)
    send_object(g1, b1)
    expect_ihave(g2, b1_id)

    bad_tx = signed_tx([(cb_id, 0, bob)], [(bob.pubkey, 50)])  # signed with wrong key
    bad_tx_id = object_id(bad_tx)
    b2 = make_block([bad_tx_id], previd=b1_id, created=b1['created'] + 1)
    b2_id = object_id(b2)

    send_object(g1, b2)
    req = read_type(g1, 'getobject')
    check(req['objectid'] == bad_tx_id, f'expected getobject for {bad_tx_id}, got {req}')
    send_object(g1, bad_tx)

    err1 = read_type(g1, 'error')
    err2 = read_type(g1, 'error')
    check(
        {err1.get('name'), err2.get('name')} == {'INVALID_TX_SIGNATURE', 'INVALID_ANCESTRY'},
        f'expected INVALID_TX_SIGNATURE and INVALID_ANCESTRY, got {err1} and {err2}',
    )
    g1.expect_closed()
    expect_no_ihave(g2, b2_id)


def test_block_unknown_parent_gives_unknown_object():
    """A block whose parent is not known gives UNKNOWN_OBJECT without closing."""
    g1, g2 = handshake(), handshake()
    request_peers(g2)
    b = make_block([], previd='ee' * 32)
    send_object(g1, b)
    err = read_type(g1, 'error')
    check(err.get('name') == 'UNKNOWN_OBJECT', f'expected UNKNOWN_OBJECT, got {err}')
    request_peers(g1)
    expect_no_ihave(g2, object_id(b))


def test_invalid_blocks_rejected_and_not_gossiped():
    """Invalid blocks give the right error, close the connection and are not gossiped."""
    setup = handshake()
    alice, bob = Wallet(), Wallet()
    cb1 = {'type': 'transaction', 'height': 1, 'outputs': [{'pubkey': alice.pubkey, 'value': 100}]}
    cb1_id = object_id(cb1)
    b1 = make_block([cb1_id], previd=GENESIS_ID, created=GENESIS_CREATED + 1)
    b1_id = object_id(b1)
    send_object(setup, cb1)
    send_object(setup, b1)
    check(get_object(setup, b1_id) == b1, 'setup block not stored')

    unconfirmed_cb = new_coinbase(alice, value=100)
    spend_unconfirmed = signed_tx([(object_id(unconfirmed_cb), 0, alice)], [(bob.pubkey, 50)])
    ds1 = signed_tx([(cb1_id, 0, alice)], [(bob.pubkey, 40)])
    ds2 = signed_tx([(cb1_id, 0, alice)], [(alice.pubkey, 40)])
    cb2 = {'type': 'transaction', 'height': 2, 'outputs': [{'pubkey': alice.pubkey, 'value': BLOCK_REWARD}]}
    spend_same_cb = signed_tx([(object_id(cb2), 0, alice)], [(bob.pubkey, 10)])
    cb_greedy = {'type': 'transaction', 'height': 2, 'outputs': [{'pubkey': bob.pubkey, 'value': BLOCK_REWARD + 1}]}
    cb_bad_height = {'type': 'transaction', 'height': 9, 'outputs': [{'pubkey': bob.pubkey, 'value': BLOCK_REWARD}]}

    for obj in (unconfirmed_cb, spend_unconfirmed, ds1, ds2, cb2, spend_same_cb, cb_greedy, cb_bad_height):
        send_object(setup, obj)
        get_object(setup, object_id(obj))

    cases = [
        ('wrong target', mine_block(dict(EXAMPLE_BLOCK, T='0000abd' + '0' * 57)), 'INVALID_FORMAT'),
        ('invalid pow', unmine_block(EXAMPLE_BLOCK), 'INVALID_BLOCK_POW'),
        ('fake genesis', make_block([], previd=None), 'INVALID_GENESIS'),
        ('timestamp <= parent', make_block([], previd=b1_id, created=b1['created']),
         'INVALID_BLOCK_TIMESTAMP'),
        ('timestamp in future', make_block([], previd=b1_id, created=10 ** 12),
         'INVALID_BLOCK_TIMESTAMP'),
        ('double spend in block',
         make_block([object_id(ds1), object_id(ds2)], previd=b1_id, created=b1['created'] + 1),
         'INVALID_TX_OUTPOINT'),
        ('spend not in utxo',
         make_block([object_id(spend_unconfirmed)], previd=b1_id, created=b1['created'] + 1),
         'INVALID_TX_OUTPOINT'),
        ('spend same-block coinbase',
         make_block([object_id(cb2), object_id(spend_same_cb)], previd=b1_id, created=b1['created'] + 1),
         'INVALID_TX_OUTPOINT'),
        ('coinbase exceeds reward',
         make_block([object_id(cb_greedy)], previd=b1_id, created=b1['created'] + 1),
         'INVALID_BLOCK_COINBASE'),
        ('coinbase wrong height',
         make_block([object_id(cb_bad_height)], previd=b1_id, created=b1['created'] + 1),
         'INVALID_BLOCK_COINBASE'),
    ]

    for desc, bad_block, error in cases:
        g1, g2 = handshake(), handshake()
        request_peers(g2)
        send_object(g1, bad_block)
        try:
            expect_error(g1, error)
            expect_no_ihave(g2, object_id(bad_block))
        except AssertionError as e:
            raise AssertionError(f'{desc}: {e}')
        g1.close()
        g2.close()


def test_block_missing_tx_timeout_unfindable():
    """A block whose transaction does not arrive within 5s gives UNFINDABLE_OBJECT (slow)."""
    g1, g2 = handshake(), handshake()
    request_peers(g2)
    missing_txid = object_id({'nonexistent': random.random()})
    b = make_block([missing_txid])
    send_object(g1, b)
    req = read_type(g1, 'getobject')
    check(req == {'type': 'getobject', 'objectid': missing_txid}, f'unexpected getobject: {req}')
    err = read_type(g1, 'error', timeout=7.0)
    check(err.get('name') == 'UNFINDABLE_OBJECT', f'expected UNFINDABLE_OBJECT, got {err}')
    request_peers(g1)  # connection stays open
    expect_no_ihave(g2, object_id(b))


TASK3_TESTS = [
    test_task3_example_block_accepted,
    test_valid_block_gossiped,
    test_block_fetches_missing_transaction,
    test_block_with_invalid_fetched_tx_gives_invalid_ancestry,
    test_block_unknown_parent_gives_unknown_object,
    test_invalid_blocks_rejected_and_not_gossiped,
]


FAST_TESTS = [
    test_handshake_and_getpeers,
    test_hello_without_agent_is_accepted,
    test_non_canonical_json_is_accepted,
    test_defragmentation,
    test_multiple_messages_in_one_packet,
    test_message_before_hello,
    test_second_hello,
    test_invalid_messages_before_hello,
    test_invalid_messages_after_hello,
    test_invalid_peer_addresses,
    test_too_many_peers,
    test_valid_peers_messages_keep_connection_open,
    test_peer_persistence,
    test_peers_reply_is_limited_to_30,
    test_simultaneous_connections,
    test_reconnect,
    test_ignored_valid_messages,
    test_idle_connection_stays_open,
    test_node_still_alive,
]


def main():
    """Run all tests, print a summary and exit with status 1 on failure."""
    try:
        socket.create_connection((HOST, PORT), timeout=3).close()
    except OSError as e:
        print(f'Cannot connect to {HOST}:{PORT} ({e}). Is the node running?')
        sys.exit(2)

    tests = list(FAST_TESTS) + TASK2_TESTS + TASK3_TESTS
    if '--slow' in sys.argv:
        tests.append(test_block_missing_tx_timeout_unfindable)
        tests.append(test_handshake_timeout)

    failed = []
    for test in tests:
        try:
            test()
            print(f'✅ {test.__name__}')
        except Exception as e:
            failed.append(test.__name__)
            print(f'❌ {test.__name__}: {e!r}')
        finally:
            for client in _open_clients:
                client.close()
            _open_clients.clear()

    print()
    if failed:
        print(f'{len(failed)} of {len(tests)} tests FAILED: {", ".join(failed)}')
        sys.exit(1)
    print(f'🎉 All {len(tests)} tests passed.')


if __name__ == '__main__':
    main()