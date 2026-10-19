import json
import socket
import time

HOST = '127.0.0.1'
PORT = 18018


def receive_lines(sock, num_lines=1, timeout=3.0):
    """Reads a specified number of line-delimited JSON messages from the socket."""
    buffer = ''
    lines = []
    sock.settimeout(timeout)
    start_time = time.time()
    while len(lines) < num_lines and (time.time() - start_time) < timeout:
        try:
            data = sock.recv(1024).decode('utf-8')
            if not data:
                break
            buffer += data
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                if line.strip():
                    lines.append(line.strip())
        except socket.timeout:
            break
        except Exception:
            break
    return lines


def test_1_handshake_and_getpeers():
    print('--- Test 1: Valid Handshake & getpeers ---')
    s = socket.create_connection((HOST, PORT), timeout=5)
    init_lines = receive_lines(s, 2)
    assert len(init_lines) >= 2, 'Node failed to send initial hello and getpeers'
    assert 'hello' in init_lines[0]

    s.sendall(b'{"type":"hello","version":"0.10.0"}\n')
    s.sendall(b'{"type":"getpeers"}\n')

    resp = receive_lines(s, 1)[0]
    assert 'peers' in resp
    s.close()
    print('✅ Test 1 PASSED')


def test_2_defragmentation():
    print('--- Test 2: Message Defragmentation (Fragmented Packet) ---')
    s = socket.create_connection((HOST, PORT), timeout=5)
    receive_lines(s, 2)

    s.sendall(b'{"type":"hello","version":"0.10.0"}\n')

    # Send message split into two parts with a 0.1s delay
    s.sendall(b'{"type":"ge')
    time.sleep(0.1)
    s.sendall(b'tpeers"}\n')

    resp = receive_lines(s, 1)[0]
    assert 'peers' in resp
    s.close()
    print('✅ Test 2 PASSED')


def test_3_message_before_hello():
    print('--- Test 3: Message sent before Hello (INVALID_HANDSHAKE) ---')
    s = socket.create_connection((HOST, PORT), timeout=5)
    receive_lines(s, 2)

    s.sendall(b'{"type":"getpeers"}\n')
    resp = receive_lines(s, 1)[0]
    assert 'INVALID_HANDSHAKE' in resp
    s.close()
    print('✅ Test 3 PASSED')


def test_4_second_hello():
    print('--- Test 4: Second Hello Message (INVALID_HANDSHAKE) ---')
    s = socket.create_connection((HOST, PORT), timeout=5)
    receive_lines(s, 2)

    s.sendall(b'{"type":"hello","version":"0.10.0"}\n')
    s.sendall(b'{"type":"hello","version":"0.10.0"}\n')

    resp = receive_lines(s, 1)[0]
    assert 'INVALID_HANDSHAKE' in resp
    s.close()
    print('✅ Test 4 PASSED')


def test_5_invalid_messages_format():
    print('--- Test 5: Invalid Message Formats (INVALID_FORMAT) ---')
    # Invalid example payloads from Task 1 specification
    invalid_payloads = [
        b'Wbgygvf7rgtyv7tfbgy{{{\n',
        b'{"type": "diufygeuybhv"}\n',
        b'{"type": "hello"}\n',
        b'{"type": "hello", "version":"jd3.x"}\n',
        b'{"type": "hello", "version":"5.8.2"}\n',
    ]

    for payload in invalid_payloads:
        s = socket.create_connection((HOST, PORT), timeout=5)
        receive_lines(s, 2)
        s.sendall(payload)
        resp = receive_lines(s, 1)
        assert len(resp) > 0 and 'INVALID_FORMAT' in resp[0]
        s.close()
    print('✅ Test 5 PASSED')


def test_6_invalid_peer_addresses():
    print('--- Test 6: Invalid Peer Addresses (INVALID_FORMAT) ---')
    # Invalid peer address examples from Task 1 specification
    invalid_peers = [
        '256.2.3.4:18018',
        '1.2.3.4.5:678',
        '1.2.3.4:2000000',
        '192.168.0.2:18018',
        'nodotindomain:1234',
        'kermanode.net',
    ]

    for peer in invalid_peers:
        s = socket.create_connection((HOST, PORT), timeout=5)
        receive_lines(s, 2)
        s.sendall(b'{"type":"hello","version":"0.10.0"}\n')

        msg = json.dumps({'type': 'peers', 'peers': [peer]}) + '\n'
        s.sendall(msg.encode('utf-8'))

        resp = receive_lines(s, 1)
        assert len(resp) > 0 and 'INVALID_FORMAT' in resp[0]
        s.close()
    print('✅ Test 6 PASSED')


def test_7_peer_persistence():
    print('--- Test 7: Peer Storage & Persistence ---')
    # Connection 1: Send a valid peer address
    s1 = socket.create_connection((HOST, PORT), timeout=5)
    receive_lines(s1, 2)
    s1.sendall(b'{"type":"hello","version":"0.10.0"}\n')

    valid_test_peer = '138.197.191.170:18018'
    msg = json.dumps({'type': 'peers', 'peers': [valid_test_peer]}) + '\n'
    s1.sendall(msg.encode('utf-8'))
    time.sleep(0.2)
    s1.close()

    # Connection 2: Reconnect and request peers
    s2 = socket.create_connection((HOST, PORT), timeout=5)
    receive_lines(s2, 2)
    s2.sendall(b'{"type":"hello","version":"0.10.0"}\n')
    s2.sendall(b'{"type":"getpeers"}\n')

    resp = receive_lines(s2, 1)[0]
    assert valid_test_peer in resp
    s2.close()
    print('✅ Test 7 PASSED')


def test_8_simultaneous_connections():
    print('--- Test 8: Simultaneous Connections ---')
    s1 = socket.create_connection((HOST, PORT), timeout=5)
    s2 = socket.create_connection((HOST, PORT), timeout=5)

    lines1 = receive_lines(s1, 2)
    lines2 = receive_lines(s2, 2)

    assert len(lines1) >= 2 and len(lines2) >= 2

    s1.sendall(b'{"type":"hello","version":"0.10.0"}\n')
    s2.sendall(b'{"type":"hello","version":"0.10.0"}\n')

    s1.close()
    s2.close()
    print('✅ Test 8 PASSED')


def test_9_ignored_valid_messages():
    print('--- Test 9: Ignore Unknown/Future Valid Messages (getchaintip) ---')
    s = socket.create_connection((HOST, PORT), timeout=5)
    receive_lines(s, 2)

    s.sendall(b'{"type":"hello","version":"0.10.0"}\n')
    s.sendall(b'{"type":"getchaintip"}\n')

    # Connection should remain open and accept subsequent valid messages
    s.sendall(b'{"type":"getpeers"}\n')
    resp = receive_lines(s, 1)[0]
    assert 'peers' in resp
    s.close()
    print('✅ Test 9 PASSED')


if __name__ == '__main__':
    test_1_handshake_and_getpeers()
    test_2_defragmentation()
    test_3_message_before_hello()
    test_4_second_hello()
    test_5_invalid_messages_format()
    test_6_invalid_peer_addresses()
    test_7_peer_persistence()
    test_8_simultaneous_connections()
    test_9_ignored_valid_messages()
    print('\n🎉 ALL TASK 1 GRADER TESTS PASSED SUCCESSFULLY!')