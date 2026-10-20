"""Message definitions, canonical JSON encoding and validation for the Kerma protocol."""

import ipaddress
import json
import re
from typing import Any, Dict, Tuple

# All message types defined by the protocol.
VALID_MESSAGE_TYPES = {
    'hello',
    'getpeers',
    'peers',
    'error',
    'getchaintip',
    'chaintip',
    'getobject',
    'object',
    'ihaveobject',
    'mempool',
    'getmempool',
}

# Allowed keys per message type. Types not listed here are not validated in detail.
ALLOWED_KEYS = {
    'hello': {'type', 'version', 'agent'},
    'getpeers': {'type'},
    'peers': {'type', 'peers'},
    'error': {'type', 'name', 'msg'},
}

MAX_PEERS_PER_MESSAGE = 30
MAX_AGENT_LEN = 128

VERSION_RE = re.compile(r'0\.10\.[0-9]')
DNS_RE = re.compile(r'[a-zA-Z0-9.\-_]{3,50}')
PORT_RE = re.compile(r'[0-9]{1,5}')
IPV4_RE = re.compile(r'[0-9]{1,3}(\.[0-9]{1,3}){3}')


def serialize_canonical_json(data: Dict[str, Any]) -> bytes:
    """Encode a message as canonical JSON (sorted keys, compact separators) terminated by '\\n'."""
    json_str = json.dumps(
        data, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    )
    return (json_str + '\n').encode('utf-8')


def _is_printable_ascii(s: str) -> bool:
    """Return True if every character is printable ASCII (0x20 to 0x7e)."""
    return all(0x20 <= ord(c) <= 0x7E for c in s)


def validate_message(msg: Any) -> Tuple[bool, str, str]:
    """Validate the structure of a parsed message.

    Returns (ok, error_name, detail); on success (True, '', '').
    """
    if not isinstance(msg, dict):
        return False, 'INVALID_FORMAT', 'Message is not a JSON object'

    msg_type = msg.get('type')
    if not isinstance(msg_type, str):
        return False, 'INVALID_FORMAT', 'Missing or invalid "type" key'
    if msg_type not in VALID_MESSAGE_TYPES:
        return False, 'INVALID_FORMAT', f'Unknown message type "{msg_type}"'

    allowed = ALLOWED_KEYS.get(msg_type)
    if allowed is not None:
        extra = set(msg) - allowed
        if extra:
            return False, 'INVALID_FORMAT', f'Unexpected keys: {sorted(extra)}'

    if msg_type == 'hello':
        version = msg.get('version')
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            return False, 'INVALID_FORMAT', 'Missing or invalid version'
        # The agent key is optional, but must be valid if present.
        if 'agent' in msg:
            agent = msg['agent']
            if (
                    not isinstance(agent, str)
                    or len(agent) > MAX_AGENT_LEN
                    or not _is_printable_ascii(agent)
            ):
                return False, 'INVALID_FORMAT', 'Invalid agent'

    elif msg_type == 'peers':
        peers = msg.get('peers')
        if not isinstance(peers, list):
            return False, 'INVALID_FORMAT', 'Missing or invalid peers list'
        if len(peers) > MAX_PEERS_PER_MESSAGE:
            return False, 'INVALID_FORMAT', 'More than 30 peers'
        for peer in peers:
            if not validate_peer_address(peer):
                return False, 'INVALID_FORMAT', f'Invalid peer address: {peer!r}'

    elif msg_type == 'error':
        if not isinstance(msg.get('name'), str):
            return False, 'INVALID_FORMAT', 'Missing or invalid error name'
        if 'msg' in msg and not isinstance(msg['msg'], str):
            return False, 'INVALID_FORMAT', 'Invalid error msg'

    return True, '', ''


def validate_peer_address(peer_str: Any) -> bool:
    """Return True if peer_str is a valid 'host:port' (public IPv4 or DNS name, port 1-65535)."""
    if not isinstance(peer_str, str):
        return False

    host, sep, port_str = peer_str.rpartition(':')
    if not sep or not host:
        return False

    if not PORT_RE.fullmatch(port_str):
        return False
    if not (1 <= int(port_str) <= 65535):
        return False

    return is_valid_ipv4(host) or is_valid_dns(host)


def is_valid_ipv4(host: str) -> bool:
    """Return True if host is a syntactically valid, publicly routable IPv4 address."""
    if not IPV4_RE.fullmatch(host):
        return False
    try:
        return ipaddress.IPv4Address(host).is_global
    except ValueError:
        return False


def is_valid_dns(host: str) -> bool:
    """Return True if host is a valid DNS name according to the Kerma rules.

    Rules: matches [a-zA-Z0-9.-_]{3,50}, contains a dot that is neither the
    first nor the last character, and contains at least one letter.
    """
    if not DNS_RE.fullmatch(host):
        return False
    if '.' not in host[1:-1]:
        return False
    if not re.search(r'[a-zA-Z]', host):
        return False
    return True