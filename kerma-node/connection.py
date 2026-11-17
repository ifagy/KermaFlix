"""Handling of a single peer connection: handshake, parsing and message dispatch."""

import asyncio
import json
import logging
import time
from typing import Any, Optional, Tuple

from blocks import execute_block, validate_block_header
from objects import KermaError, object_id, validate_transaction
from pending import INVALID, UNFINDABLE, VALID, PendingObjects
from protocol import serialize_canonical_json, validate_message

logger = logging.getLogger('Connection')

# Seconds to wait for the remote hello message.
HANDSHAKE_TIMEOUT = 20.0
# Maximum length of a single message line in bytes.
STREAM_LIMIT = 1024 * 1024
# Seconds to wait for the missing transactions of a block.
TX_FETCH_TIMEOUT = 5.0
# Seconds to wait for a parent block that is still being validated.
PARENT_TIMEOUT = 10.0


def _reject_constant(name: str):
    """json.loads hook: NaN and Infinity are not valid JSON."""
    raise ValueError(f'Invalid JSON constant {name}')


class ConnectionHandler:
    """Runs the protocol for one TCP connection (inbound or outbound)."""

    def __init__(
            self,
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            peer_manager,
            is_outbound: bool = False,
            remote_peer_str: Optional[str] = None,
            object_db=None,
            network=None,
    ):
        """Store the stream objects and connection metadata.

        remote_peer_str is the 'host:port' we connected to (outbound only).
        object_db is the ObjectDB used for object exchange.
        network provides register(handler), unregister(handler) and
        broadcast(msg, exclude) for gossiping (usually the KermaNode).
        """
        self.reader = reader
        self.writer = writer
        self.peer_manager = peer_manager
        self.is_outbound = is_outbound
        self.remote_peer_str = remote_peer_str
        self.object_db = object_db
        self.network = network
        # Shared bookkeeping of validations that wait for other objects.
        self.pending = getattr(network, 'pending', None) or PendingObjects()
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
            if self.network is not None:
                self.network.unregister(self)
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
            return json.loads(line.decode('utf-8'), parse_constant=_reject_constant), None
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
        if self.network is not None:
            self.network.register(self)

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

            elif msg_type in ('getobject', 'ihaveobject', 'object'):
                oid = object_id(msg['object']) if msg_type == 'object' else None
                try:
                    await self._handle_object_message(msg)
                except KermaError as e:
                    await self._report_error(e, oid)
                    if e.fatal:
                        return

            else:
                # Valid message types without handling are ignored.
                logger.debug(f'[{self.label}] Ignoring message type: {msg_type}')

    async def _report_error(self, e: KermaError, oid: Optional[str] = None) -> None:
        """Send an error for an object (oid) to the peer.

        Validations waiting for oid are notified first, so that their errors
        (INVALID_ANCESTRY / UNFINDABLE_OBJECT) are still sent. For fatal errors
        the connection is closed afterwards (and an outbound peer is forgotten).
        """
        logger.warning(f'[{self.label}] Sending {e.name} ({e.msg})')
        await self.send_msg({'type': 'error', 'name': e.name, 'msg': e.msg})
        if oid is not None:
            await self.pending.fail(oid, INVALID if e.fatal else UNFINDABLE)
        if e.fatal:
            if self.is_outbound and self.remote_peer_str:
                self.peer_manager.remove_peer(self.remote_peer_str)
            self.close()

    # ---------- Object exchange ----------

    async def _handle_object_message(self, msg: dict) -> None:
        """Handle getobject, ihaveobject and object messages.

        Raises KermaError if an error has to be reported to the peer.
        """
        if self.object_db is None:
            return
        msg_type = msg['type']

        if msg_type == 'getobject':
            obj = self.object_db.get(msg['objectid'])
            if obj is None:
                raise KermaError(
                    'UNKNOWN_OBJECT', f'Object {msg["objectid"]} is not known'
                )
            await self.send_msg({'type': 'object', 'object': obj})

        elif msg_type == 'ihaveobject':
            if not self.object_db.has(msg['objectid']):
                await self.send_msg({'type': 'getobject', 'objectid': msg['objectid']})

        elif msg_type == 'object':
            await self._receive_object(msg['object'])

    async def _receive_object(self, obj: dict) -> None:
        """Validate, store and gossip a received object; known objects are ignored."""
        oid = object_id(obj)
        if self.object_db.has(oid):
            logger.debug(f'[{self.label}] Ignoring known object {oid}')
            return

        obj_type = obj.get('type')
        if obj_type == 'transaction':
            validate_transaction(obj, self.object_db)
            self.object_db.put(obj)
            await self._object_stored(oid, obj_type)
        elif obj_type == 'block':
            await self._receive_block(obj, oid)
        else:
            raise KermaError('INVALID_FORMAT', f'Unknown object type {obj_type!r}')

    async def _object_stored(self, oid: str, obj_type: str) -> None:
        """Notify waiting validations about a new valid object and gossip it."""
        logger.info(f'[{self.label}] Stored new valid {obj_type} {oid}')
        self.pending.resolve(oid, VALID)
        await self._broadcast({'type': 'ihaveobject', 'objectid': oid})

    async def _broadcast(self, msg: dict) -> None:
        """Send msg to all connected peers (or only this peer without a network)."""
        if self.network is not None:
            await self.network.broadcast(msg)
        else:
            await self.send_msg(msg)

    # ---------- Blocks ----------

    async def _receive_block(self, block: dict, oid: str) -> None:
        """Validate a new block; fetch missing transactions if necessary.

        If the parent and all transactions are known, the block is validated
        right away. Otherwise validation continues in the background and the
        result (stored + gossiped, or an error) is reported later.
        """
        validate_block_header(block, oid)
        if self.pending.in_progress(oid):
            return  # the same block is already being validated

        previd = block['previd']
        parent_known = self.object_db.has(previd)
        if not parent_known and not self.pending.in_progress(previd):
            raise KermaError('UNKNOWN_OBJECT', f'Parent block {previd} is not known')

        missing = [t for t in dict.fromkeys(block['txids']) if not self.object_db.has(t)]
        if parent_known and not missing:
            await self._finish_block(block, oid)
            return

        self.pending.start(oid)
        self._spawn(self._validate_block_later(block, oid, missing))

    async def _validate_block_later(self, block: dict, oid: str, missing: list) -> None:
        """Wait for the parent and the missing transactions, then finish validation."""
        try:
            previd = block['previd']
            if not self.object_db.has(previd):
                if not self.pending.in_progress(previd):
                    raise KermaError(
                        'UNFINDABLE_OBJECT', f'Parent block {previd} could not be verified'
                    )
                await self.pending.wait_all([previd], PARENT_TIMEOUT)

            missing = [t for t in missing if not self.object_db.has(t)]
            if missing:
                async def request_missing():
                    for txid in missing:
                        await self._broadcast({'type': 'getobject', 'objectid': txid})

                await self.pending.wait_all(missing, TX_FETCH_TIMEOUT, request_missing)

            await self._finish_block(block, oid)
        except KermaError as e:
            await self._report_error(e, oid)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f'[{self.label}] Unexpected error validating block {oid}')
            self.pending.resolve(oid, UNFINDABLE)

    async def _finish_block(self, block: dict, oid: str) -> None:
        """Execute the block on its parent's UTXO set; store and gossip it if valid."""
        previd = block['previd']
        parent = self.object_db.get(previd)
        state = self.object_db.get_block_state(previd)
        if parent is None or parent.get('type') != 'block' or state is None:
            raise KermaError('INVALID_FORMAT', f'previd {previd} does not refer to a block')
        parent_height, parent_utxo = state

        txs = [self.object_db.get(txid) for txid in block['txids']]
        height, utxo = execute_block(
            block, parent, parent_height, parent_utxo, txs,
            self.object_db.get, time.time(),
        )
        self.object_db.put_block(block, height, utxo)
        await self._object_stored(oid, f'block (height {height})')

    def _spawn(self, coro) -> None:
        """Run a background task (tracked by the node if available)."""
        if self.network is not None and hasattr(self.network, '_spawn'):
            self.network._spawn(coro)
        else:
            asyncio.ensure_future(coro)