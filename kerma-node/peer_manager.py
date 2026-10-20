"""Storage and selection of known peers."""

import json
import logging
import os
import random
import time
from typing import Dict, List, Optional, Set

from protocol import validate_peer_address

logger = logging.getLogger('PeerManager')

# Hard-coded bootstrap peers used when no peers are known.
DEFAULT_BOOTSTRAP_PEERS = ['128.130.122.101:18018']
# Upper bound for the number of stored peers.
MAX_KNOWN_PEERS = 1000
# Number of failed connection attempts after which a peer is forgotten.
MAX_FAILURES = 3
# Seconds to wait before connecting to a newly learned peer.
CONNECT_GRACE = 30.0


class PeerManager:
    """Keeps the set of known peers, persists it to disk and selects peers to use."""

    def __init__(
            self,
            storage_path: Optional[str] = None,
            my_address: Optional[str] = None,
    ):
        """Create the manager, load stored peers and make sure bootstrap peers exist.

        storage_path defaults to the PEERS_FILE environment variable or 'peers.json'.
        my_address is this node's own 'host:port' as advertised to other peers.
        """
        self.storage_path = storage_path or os.environ.get(
            'PEERS_FILE', 'peers.json'
        )
        self.my_address = my_address
        self.known_peers: Set[str] = set()
        self.added_at: Dict[str, float] = {}
        self.failures: Dict[str, int] = {}

        self.ensure_bootstrap()
        self.load_peers()

    # ---------- Internal helpers ----------

    def _insert(self, peer: str, eligible_now: bool = False) -> None:
        """Add a peer and reset its failure count.

        If eligible_now is True the peer may be connected to immediately,
        otherwise only after CONNECT_GRACE seconds.
        """
        self.known_peers.add(peer)
        now = time.time()
        self.added_at[peer] = now - CONNECT_GRACE if eligible_now else now
        self.failures.pop(peer, None)

    def _evict_one(self) -> None:
        """Remove the peer with the most failures (oldest one on a tie)."""
        victim = max(
            self.known_peers,
            key=lambda p: (self.failures.get(p, 0), -self.added_at.get(p, 0.0)),
        )
        self.known_peers.discard(victim)
        self.added_at.pop(victim, None)
        self.failures.pop(victim, None)

    # ---------- Persistence ----------

    def load_peers(self) -> None:
        """Load valid peers from the storage file into the known peers."""
        if not os.path.exists(self.storage_path):
            return
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                for p in data:
                    if (
                            isinstance(p, str)
                            and validate_peer_address(p)
                            and p != self.my_address
                            and p not in self.known_peers
                    ):
                        self._insert(p, eligible_now=True)
            logger.info(
                f'Loaded {len(self.known_peers)} known peers from {self.storage_path}'
            )
        except Exception as e:
            logger.error(f'Failed to load peers file: {e}')

    def save_peers(self) -> None:
        """Write the known peers to the storage file (atomic replace)."""
        try:
            directory = os.path.dirname(self.storage_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp_path = self.storage_path + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(sorted(self.known_peers), f, indent=2)
            os.replace(tmp_path, self.storage_path)
        except Exception as e:
            logger.error(f'Failed to save peers file: {e}')

    # ---------- Management ----------

    def ensure_bootstrap(self) -> None:
        """Add the hard-coded bootstrap peers if no peers are known."""
        if not self.known_peers:
            for p in DEFAULT_BOOTSTRAP_PEERS:
                if p != self.my_address:
                    self._insert(p, eligible_now=True)

    def add_peers(self, peers: List[str]) -> bool:
        """Add valid, unknown peers (never our own address) and save the list.

        Returns True if at least one peer was added.
        """
        added = False
        for p in peers:
            if (
                    validate_peer_address(p)
                    and p != self.my_address
                    and p not in self.known_peers
            ):
                if len(self.known_peers) >= MAX_KNOWN_PEERS:
                    self._evict_one()
                self._insert(p)
                added = True
        if added:
            self.save_peers()
        return added

    def remove_peer(self, peer: str) -> None:
        """Forget a peer and save the list."""
        if peer in self.known_peers:
            self.known_peers.discard(peer)
            self.added_at.pop(peer, None)
            self.failures.pop(peer, None)
            self.save_peers()

    def record_failure(self, peer: str) -> None:
        """Count a failed connection attempt; forget the peer after MAX_FAILURES."""
        if peer not in self.known_peers:
            return
        self.failures[peer] = self.failures.get(peer, 0) + 1
        if self.failures[peer] >= MAX_FAILURES:
            logger.info(f'Forgetting unreachable peer {peer}')
            self.remove_peer(peer)

    def record_success(self, peer: str) -> None:
        """Reset the failure count of a peer after a successful handshake."""
        self.failures.pop(peer, None)

    # ---------- Selection ----------

    def get_connect_candidates(self, exclude: Set[str]) -> List[str]:
        """Return peers we may connect to, fewest failures first.

        Excludes our own address, the given peers and peers that are still
        within the CONNECT_GRACE period.
        """
        now = time.time()
        candidates = [
            p
            for p in self.known_peers
            if p != self.my_address
               and p not in exclude
               and now - self.added_at.get(p, 0.0) >= CONNECT_GRACE
        ]
        random.shuffle(candidates)
        candidates.sort(key=lambda p: self.failures.get(p, 0))
        return candidates

    def get_peers_for_msg(self) -> List[str]:
        """Build the peer list for a 'peers' message (at most 30 entries).

        Our own address comes first. The remaining slots are filled with peers
        that have the fewest failures, preferring the most recently added ones.
        """
        others = sorted(
            (p for p in self.known_peers if p != self.my_address),
            key=lambda p: (self.failures.get(p, 0), -self.added_at.get(p, 0.0)),
        )
        if self.my_address:
            return [self.my_address] + others[:29]
        return others[:30]