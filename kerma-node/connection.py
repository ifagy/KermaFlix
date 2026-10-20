"""Handling of a single peer connection: handshake, parsing and message dispatch."""

import asyncio
import json
import logging
from typing import Any, Optional, Tuple

from protocol import serialize_canonical_json, validate_message

logger = logging.getLogger('Connection')

# Seconds to wait for the remote hello message.
HANDSHAKE_TIMEOUT = 20.0
# Maximum length of a single message line in bytes.
STREAM_LIMIT = 1024 * 1024


class ConnectionHandler:
    """Runs the protocol for one TCP connection (inbound or outbound)."""

    def __init__(
            self,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            peer_manager,
            is_outbound: bool = False,
            remote_peer_str: Optional[str] = None,
    ):
        """Store the stream objects and connection metadata.

        remote_peer_str is the 'host:port' we connected to (outbound only).
        """
        self.reader = reader
        self.writer = writer
        self.peer_manager = peer_manager
        self.is_outbound = is_outbound
        self.remote_peer_str = remote_peer_str
        self.handshake_done = False
        self.is_closed = False
        self.label = remote_peer_str or str(writer.get_extra_info('peername'))

    # ---------- Sending ----------

    async def send_msg(self, msg: dict) -> None:
        """Send a message as canonical JSON; close the connection if sending fails."""
        if self.is_closed:
            return
        try:
            self.writer.write(serialize_canonical_json(msg))
            await self.writer.drain()
        except Exception as e:
            logger.warning(f'[{self.label}] Error sending message: {e}')
            self.close()

    async def send_error_and_close(self, error_name: str, msg: str = '') -> None:
        """Send an error message and close the connection.

        For outbound connections the peer is also removed from the known peers.
        """
        logger.warning(f'[{self.label}] Sending {error_name} ({msg}) and closing.')
        await self.send_msg(
            {'type': 'error', 'name': error_name, 'msg': msg or error_name}
        )
        if self.is_outbound and self.remote_peer_str:
            self.peer_manager.remove_peer(self.remote_peer_str)
        self.close()

    def close(self) -> None:
        """Close the underlying stream (safe to call multiple times)."""
        if not self.is_closed:
            self.is_closed = True
            try:
                self.writer.close()
            except Exception:
                pass

    # ---------- Receiving ----------

    @staticmethod
    def _parse_line(line: bytes) -> Tuple[Any, Optional[str]]:
        """Decode and parse one line as JSON.

        Returns (message, None) on success or (None, error_text) on failure.
        """
        try:
            return json.loads(line.decode('utf-8')), None
        except Exception as e:
            return None, f'Invalid JSON: {e}'

    async def _read_first_line(self) -> Optional[bytes]:
        """Read the next non-empty line; return None if the connection was closed."""
        while True:
            line = await self.reader.readline()
            if not line:
                return None
            if line.strip():
                return line

    # ---------- Main flow ----------

    async def handle(self) -> None:
        """Run the connection until it ends and always close it afterwards."""
        try:
            await self._run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f'[{self.label}] Unexpected error in connection')
        finally:
            self.close()

    async def _run(self) -> None:
        """Perform the handshake and process incoming messages."""
        # Send hello followed by getpeers right away.
        await self.send_msg(
            {'type': 'hello', 'version': '0.10.0', 'agent': 'KermaNode-Python'}
        )
        await self.send_msg({'type': 'getpeers'})

        # Wait for the remote hello (at most HANDSHAKE_TIMEOUT seconds).
        try:
            line = await asyncio.wait_for(
                self._read_first_line(), timeout=HANDSHAKE_TIMEOUT
            )
        except asyncio.TimeoutError:
            await self.send_error_and_close(
                'INVALID_HANDSHAKE', 'No hello received within 20 seconds'
            )
            return
        except ValueError:
            await self.send_error_and_close('INVALID_FORMAT', 'Message too long')
            return
        except OSError:
            return

        if line is None:
            return

        msg, parse_err = self._parse_line(line)
        if parse_err:
            await self.send_error_and_close('INVALID_FORMAT', parse_err)
            return

        ok, err_name, detail = validate_message(msg)
        if not ok:
            await self.send_error_and_close(err_name, detail)
            return

        if msg['type'] != 'hello':
            await self.send_error_and_close(
                'INVALID_HANDSHAKE', 'First message must be hello'
            )
            return

        self.handshake_done = True
        if self.is_outbound and self.remote_peer_str:
            self.peer_manager.record_success(self.remote_peer_str)

        # Process all further messages.
        while not self.is_closed:
            try:
                line = await self.reader.readline()
            except ValueError:
                await self.send_error_and_close('INVALID_FORMAT', 'Message too long')
                return
            except OSError:
                return

            if not line:
                return  # connection closed by the remote peer
            if not line.strip():
                continue

            msg, parse_err = self._parse_line(line)
            if parse_err:
                await self.send_error_and_close('INVALID_FORMAT', parse_err)
                return

            ok, err_name, detail = validate_message(msg)
            if not ok:
                await self.send_error_and_close(err_name, detail)
                return

            msg_type = msg['type']

            if msg_type == 'hello':
                await self.send_error_and_close(
                    'INVALID_HANDSHAKE', 'Handshake already completed'
                )
                return

            elif msg_type == 'getpeers':
                await self.send_msg(
                    {'type': 'peers', 'peers': self.peer_manager.get_peers_for_msg()}
                )

            elif msg_type == 'peers':
                self.peer_manager.add_peers(msg['peers'])

            elif msg_type == 'error':
                logger.warning(
                    f'[{self.label}] Received error: {msg.get("name")} - {msg.get("msg")}'
                )

            else:
                # Valid message types without handling are ignored.
                logger.debug(f'[{self.label}] Ignoring message type: {msg_type}')