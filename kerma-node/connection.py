import asyncio
import json
import logging
from typing import Optional
from protocol import serialize_canonical_json, validate_message

logger = logging.getLogger('Connection')

HANDSHAKE_TIMEOUT = 20.0  # Sekunden


class ConnectionHandler:

    def __init__(
            self,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            peer_manager,
            is_outbound: bool = False,
            remote_peer_str: Optional[str] = None,
    ):
        self.reader = reader
        self.writer = writer
        self.peer_manager = peer_manager
        self.is_outbound = is_outbound
        self.remote_peer_str = remote_peer_str
        self.handshake_done = False
        self.is_closed = False

    async def send_msg(self, msg: dict) -> None:
        if self.is_closed:
            return
        try:
            data = serialize_canonical_json(msg)
            self.writer.write(data)
            await self.writer.drain()
        except Exception as e:
            logger.warning(f'Error sending message: {e}')
            self.close()

    async def send_error_and_close(self, error_name: str) -> None:
        logger.warning(f'Sending error {error_name} and closing connection.')
        await self.send_msg({'type': 'error', 'name': error_name})
        self.close()

    def close(self) -> None:
        if not self.is_closed:
            self.is_closed = True
            try:
                self.writer.close()
            except Exception:
                pass

    async def handle(self) -> None:
        # 1. Sende hello und getpeers sofort nach Verbindungsaufbau
        hello_msg = {
            'type': 'hello',
            'version': '0.10.0',
            'agent': 'KermaNode-Python',
        }
        await self.send_msg(hello_msg)
        await self.send_msg({'type': 'getpeers'})

        # 2. Warte auf remote 'hello' (max. 20 Sekunden)
        try:
            line = await asyncio.wait_for(
                self.reader.readline(), timeout=HANDSHAKE_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.warning('Handshake timeout (kein hello nach 20s)')
            await self.send_error_and_close('INVALID_HANDSHAKE')
            return
        except Exception:
            self.close()
            return

        if not line:
            self.close()
            return

        # Erste Nachricht parsen
        try:
            msg_str = line.decode('utf-8').strip()
            first_msg = json.loads(msg_str)
        except Exception:
            await self.send_error_and_close('INVALID_FORMAT')
            return

        is_valid, err_name = validate_message(first_msg)
        if not is_valid:
            await self.send_error_and_close(err_name)
            return

        if first_msg.get('type') != 'hello':
            logger.warning(
                f'Ungültige erste Nachricht vor hello: {first_msg.get("type")}'
            )
            await self.send_error_and_close('INVALID_HANDSHAKE')
            return

        # Handshake erfolgreich abgeschlossen
        self.handshake_done = True

        # 3. Haupt-Schleife für weitere Nachrichten
        while not self.is_closed:
            try:
                line_bytes = await self.reader.readline()
                if not line_bytes:
                    break  # Connection closed
            except Exception:
                break

            line_str = line_bytes.decode('utf-8').strip()
            if not line_str:
                continue

            try:
                msg = json.loads(line_str)
            except Exception:
                await self.send_error_and_close('INVALID_FORMAT')
                return

            is_valid, err_name = validate_message(msg)
            if not is_valid:
                await self.send_error_and_close(err_name)
                if self.remote_peer_str and self.is_outbound:
                    self.peer_manager.remove_peer(self.remote_peer_str)
                return

            msg_type = msg.get('type')

            # Zweites Hello ist nicht erlaubt
            if msg_type == 'hello':
                await self.send_error_and_close('INVALID_HANDSHAKE')
                return

            elif msg_type == 'getpeers':
                peers_to_send = self.peer_manager.get_peers_for_msg()
                await self.send_msg({'type': 'peers', 'peers': peers_to_send})

            elif msg_type == 'peers':
                self.peer_manager.add_peers(msg['peers'])

            else:
                # Andere valide Nachrichtentypen (z.B. getchaintip) ignorieren
                logger.debug(f'Ignoriere Nachrichtentyp: {msg_type}')

        self.close()