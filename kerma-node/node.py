import asyncio
import logging
from connection import ConnectionHandler
from peer_manager import PeerManager

logger = logging.getLogger('KermaNode')


class KermaNode:

    def __init__(
            self,
            host: str = '0.0.0.0',
            port: int = 18018,
            my_address: str = 'kermanode.net:18018',
    ):
        self.host = host
        self.port = port
        self.peer_manager = PeerManager(my_address=my_address)
        self.server = None
        self.active_outbound = set()

    async def handle_incoming(
            self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        peer_addr = writer.get_extra_info('peername')
        logger.info(f'Eingehende Verbindung von {peer_addr}')
        handler = ConnectionHandler(
            reader, writer, self.peer_manager, is_outbound=False
        )
        await handler.handle()

    async def start_server(self):
        self.server = await asyncio.start_server(
            self.handle_incoming, self.host, self.port
        )
        logger.info(f'Kerma Node lauscht auf {self.host}:{self.port}')

    async def connect_to_peer(self, peer_str: str):
        if peer_str in self.active_outbound or peer_str == self.peer_manager.my_address:
            return

        self.active_outbound.add(peer_str)
        host, _, port_str = peer_str.rpartition(':')
        port = int(port_str)

        try:
            reader, writer = await asyncio.open_connection(host, port)
            handler = ConnectionHandler(
                reader,
                writer,
                self.peer_manager,
                is_outbound=True,
                remote_peer_str=peer_str,
            )
            await handler.handle()
        except Exception as e:
            logger.debug(f'Verbindung zu {peer_str} fehlgeschlagen: {e}')
            self.peer_manager.remove_peer(peer_str)
        finally:
            self.active_outbound.discard(peer_str)

    async def peer_discovery_loop(self):
        """Verbindet sich periodisch mit bekannten Peers."""
        while True:
            await asyncio.sleep(10)
            peers_to_try = [
                p
                for p in self.peer_manager.known_peers
                if p != self.peer_manager.my_address and p not in self.active_outbound
            ]
            for peer in peers_to_try[:5]:
                asyncio.create_task(self.connect_to_peer(peer))

    async def run(self):
        await self.start_server()
        asyncio.create_task(self.peer_discovery_loop())
        await self.server.serve_forever()