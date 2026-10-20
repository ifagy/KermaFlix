"""The Kerma node: TCP server, outbound connections and peer discovery."""

import asyncio
import logging
from typing import Optional

from connection import ConnectionHandler, STREAM_LIMIT
from peer_manager import PeerManager

logger = logging.getLogger('KermaNode')

# Maximum number of simultaneous outbound connections.
MAX_OUTBOUND = 8
# Seconds before an outbound connection attempt is aborted.
CONNECT_TIMEOUT = 10.0
# Seconds between two peer discovery rounds.
DISCOVERY_INTERVAL = 10.0


class KermaNode:
    """Listens for incoming connections and keeps outbound connections to known peers."""

    def __init__(
            self,
            host: str = '0.0.0.0',
            port: int = 18018,
            my_address: Optional[str] = None,
    ):
        """Create the node.

        my_address is the 'host:port' this node advertises to other peers.
        """
        self.host = host
        self.port = port
        self.peer_manager = PeerManager(my_address=my_address)
        self.server = None
        self.active_outbound = set()
        self._tasks = set()

    def _spawn(self, coro) -> None:
        """Run a coroutine as a background task and keep a reference until it finishes."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def handle_incoming(
            self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle a newly accepted incoming connection."""
        peer_addr = writer.get_extra_info('peername')
        logger.info(f'Incoming connection from {peer_addr}')
        handler = ConnectionHandler(
            reader, writer, self.peer_manager, is_outbound=False
        )
        try:
            await handler.handle()
        finally:
            handler.close()

    async def start_server(self):
        """Start listening for incoming TCP connections."""
        self.server = await asyncio.start_server(
            self.handle_incoming, self.host, self.port, limit=STREAM_LIMIT
        )
        logger.info(f'Kerma node listening on {self.host}:{self.port}')

    async def connect_to_peer(self, peer_str: str):
        """Open an outbound connection to 'host:port' and run the protocol on it.

        Connection failures are counted against the peer.
        """
        if peer_str in self.active_outbound or peer_str == self.peer_manager.my_address:
            return

        self.active_outbound.add(peer_str)
        try:
            host, _, port_str = peer_str.rpartition(':')
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, int(port_str), limit=STREAM_LIMIT),
                    timeout=CONNECT_TIMEOUT,
                )
            except (OSError, asyncio.TimeoutError) as e:
                logger.info(f'Connection to {peer_str} failed: {e!r}')
                self.peer_manager.record_failure(peer_str)
                return

            handler = ConnectionHandler(
                reader,
                writer,
                self.peer_manager,
                is_outbound=True,
                remote_peer_str=peer_str,
            )
            await handler.handle()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f'Unexpected error while connecting to {peer_str}')
        finally:
            self.active_outbound.discard(peer_str)

    async def peer_discovery_loop(self):
        """Periodically connect to known peers until MAX_OUTBOUND connections are open."""
        while True:
            await asyncio.sleep(DISCOVERY_INTERVAL)
            try:
                self.peer_manager.ensure_bootstrap()
                free_slots = MAX_OUTBOUND - len(self.active_outbound)
                if free_slots <= 0:
                    continue
                candidates = self.peer_manager.get_connect_candidates(
                    self.active_outbound
                )
                for peer in candidates[:free_slots]:
                    self._spawn(self.connect_to_peer(peer))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Error in peer discovery loop')

    async def run(self):
        """Start the server and the discovery loop and run until cancelled."""
        await self.start_server()
        self._spawn(self.peer_discovery_loop())
        async with self.server:
            await self.server.serve_forever()