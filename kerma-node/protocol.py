import json
import re
from typing import Any, Dict, Tuple

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


def serialize_canonical_json(data: Dict[str, Any]) -> bytes:
    """Kanonische JSON-Kodierung (sortierte Keys, kompakte Separatoren) mit Zeilenumbruch \\n."""
    json_str = json.dumps(
        data, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    )
    return (json_str + '\n').encode('utf-8')


def validate_message(msg: Any) -> Tuple[bool, str]:
    """Überprüft die Nachrichtenstruktur.

    Gibt (True, '') bei Erfolg zurück, sonst (False, 'INVALID_FORMAT').
    """
    if not isinstance(msg, dict):
        return False, 'INVALID_FORMAT'

    if 'type' not in msg or not isinstance(msg['type'], str):
        return False, 'INVALID_FORMAT'

    msg_type = msg['type']
    if msg_type not in VALID_MESSAGE_TYPES:
        return False, 'INVALID_FORMAT'

    if msg_type == 'hello':
        if 'version' not in msg or not isinstance(msg['version'], str):
            return False, 'INVALID_FORMAT'
        # Version muss dem Format 0.10.x entsprechen
        if not re.match(r'^0\.10\.\d+$', msg['version']):
            return False, 'INVALID_FORMAT'

    elif msg_type == 'peers':
        if 'peers' not in msg or not isinstance(msg['peers'], list):
            return False, 'INVALID_FORMAT'
        if len(msg['peers']) > 30:
            return False, 'INVALID_FORMAT'
        for peer in msg['peers']:
            if not isinstance(peer, str) or not validate_peer_address(peer):
                return False, 'INVALID_FORMAT'

    elif msg_type == 'error':
        if 'name' not in msg or not isinstance(msg['name'], str):
            return False, 'INVALID_FORMAT'

    return True, ''


def validate_peer_address(peer_str: str) -> bool:
    """Validiert Peer-Strings nach den Kerma-Spezifikationen (host:port)."""
    if not isinstance(peer_str, str) or ':' not in peer_str:
        return False

    host, _, port_str = peer_str.rpartition(':')
    if not host or not port_str:
        return False

    try:
        port = int(port_str)
        if not (1 <= port <= 65535):
            return False
    except ValueError:
        return False

    return is_valid_ipv4(host) or is_valid_dns(host)


def is_valid_ipv4(host: str) -> bool:
    """Prüft, ob host eine valide öffentliche IPv4-Adresse ist."""
    import ipaddress

    try:
        ip = ipaddress.IPv4Address(host)
        # Darf keine private, Loopback- oder Multicast-Adresse sein
        return ip.is_global and not ip.is_private and not ip.is_loopback
    except ValueError:
        return False


def is_valid_dns(host: str) -> bool:
    """Prüft DNS-Regeln:

    - Länge: 3 bis 50 Zeichen
    - Zeichen: [a-zA-Z0-9.-_]
    - Mindestens ein Punkt (nicht an Position 0 oder Ende)
    - Mindestens ein Buchstaben [a-zA-Z]
    """
    if not (3 <= len(host) <= 50):
        return False

    if not re.match(r'^[a-zA-Z0-9\.\-\_]{3,50}$', host):
        return False

    first_dot = host.find('.')
    last_dot = host.rfind('.')
    if first_dot <= 0 or last_dot >= len(host) - 1:
        return False

    if not re.search(r'[a-zA-Z]', host):
        return False

    return True