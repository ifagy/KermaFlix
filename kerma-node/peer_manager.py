import json
import logging
import os
from typing import List, Set
from protocol import validate_peer_address

logger = logging.getLogger('PeerManager')

DEFAULT_BOOTSTRAP_PEERS = {'128.130.122.101:18018'}


class PeerManager:

    def __init__(
            self,
            storage_path: str = 'peers.json',
            my_address: str = 'kermanode.net:18018',
    ):
        self.storage_path = storage_path
        self.my_address = my_address
        self.known_peers: Set[str] = set(DEFAULT_BOOTSTRAP_PEERS)
        self.load_peers()

    def load_peers(self) -> None:
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for p in data:
                            if validate_peer_address(p):
                                self.known_peers.add(p)
                logger.info(
                    f'Loaded {len(self.known_peers)} known peers from'
                    f' {self.storage_path}'
                )
            except Exception as e:
                logger.error(f'Failed to load peers file: {e}')

    def save_peers(self) -> None:
        try:
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(sorted(list(self.known_peers)), f, indent=2)
        except Exception as e:
            logger.error(f'Failed to save peers file: {e}')

    def add_peers(self, peers: List[str]) -> bool:
        added = False
        for p in peers:
            if validate_peer_address(p) and p not in self.known_peers:
                self.known_peers.add(p)
                added = True
        if added:
            self.save_peers()
        return added

    def remove_peer(self, peer: str) -> None:
        if peer in self.known_peers:
            self.known_peers.remove(peer)
            self.save_peers()

    def get_peers_for_msg(self) -> List[str]:
        """Erstellt 'peers'-Antwort mit max. 30 Peers (Eigener Node auf Position 0)."""
        res = [self.my_address]
        others = [p for p in sorted(self.known_peers) if p != self.my_address]
        res.extend(others[:29])
        return res