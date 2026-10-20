"""End-to-end tests for the Kerma node (Task 1).

Start the node first, e.g.:

    cd kerma-node && docker compose up -d --build

Then run the tests from the project root:

    python auto_test.py            # all fast tests
    python auto_test.py --slow     # additionally runs the 20 s handshake timeout test

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

    tests = list(FAST_TESTS)
    if '--slow' in sys.argv:
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