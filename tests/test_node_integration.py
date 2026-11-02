"""Integration tests: a real KermaNode on localhost with two simulated graders.

Covers object exchange, gossiping, transaction validation over the network and
persistence of the object database across node restarts.
"""

import asyncio
import json
import os
import tempfile
import unittest

import helpers  # noqa: F401  (sets up the import path)
from helpers import (
    EXAMPLE_COINBASE, EXAMPLE_COINBASE_ID, EXAMPLE_TX, EXAMPLE_TX_ID, Wallet, coinbase, spend,
)
from node import KermaNode
from object_db import ObjectDB
from objects import GENESIS_BLOCK, GENESIS_ID, object_id
from peer_manager import PeerManager

HELLO = {'type': 'hello', 'version': '0.10.0', 'agent': 'unit-test'}


class Grader:
    """A test client connected to the node."""

    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer

    @classmethod
    async def connect(cls, port):
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        g = cls(reader, writer)
        assert (await g.read())['type'] == 'hello'
        assert (await g.read())['type'] == 'getpeers'
        await g.send(HELLO)
        return g

    async def send(self, msg):
        self.writer.write(json.dumps(msg).encode() + b'\n')
        await self.writer.drain()

    async def read(self, timeout=2.0):
        line = await asyncio.wait_for(self.reader.readline(), timeout)
        if not line:
            raise ConnectionError('connection closed')
        return json.loads(line)

    async def read_type(self, msg_type, timeout=2.0):
        """Read messages until one of msg_type arrives (skips e.g. peers replies)."""
        while True:
            msg = await self.read(timeout)
            if msg['type'] == msg_type:
                return msg

    async def expect_silence(self, timeout=0.5):
        """Assert that no message (other than none) arrives within timeout."""
        try:
            msg = await self.read(timeout)
        except asyncio.TimeoutError:
            return
        raise AssertionError(f'unexpected message {msg}')

    async def expect_closed(self, timeout=2.0):
        line = await asyncio.wait_for(self.reader.readline(), timeout)
        assert line == b'', f'expected close, got {line!r}'

    async def is_alive(self):
        """The connection still works: getpeers is answered with peers."""
        await self.send({'type': 'getpeers'})
        return (await self.read_type('peers'))['type'] == 'peers'

    def close(self):
        self.writer.close()


class NodeIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp.name, 'objects.db')
        self.graders = []
        await self.start_node()

    async def start_node(self):
        self.node = KermaNode(
            host='127.0.0.1',
            port=0,
            peer_manager=PeerManager(storage_path=os.path.join(self.tmp.name, 'peers.json')),
            object_db=ObjectDB(self.db_path),
        )
        await self.node.start_server()
        self.port = self.node.server.sockets[0].getsockname()[1]

    async def stop_node(self):
        for g in self.graders:
            g.close()
        self.graders.clear()
        for h in list(self.node.connections):
            h.close()
        self.node.server.close()
        await asyncio.wait_for(self.node.server.wait_closed(), 5)
        await asyncio.sleep(0.05)
        self.node.object_db.close()

    async def asyncTearDown(self):
        await self.stop_node()
        self.tmp.cleanup()

    async def grader(self):
        g = await Grader.connect(self.port)
        self.graders.append(g)
        # Wait until the node has processed the hello and registered the connection.
        await g.is_alive()
        return g

    # ---------- Object exchange ----------

    async def test_genesis_is_available(self):
        g1 = await self.grader()
        await g1.send({'type': 'getobject', 'objectid': GENESIS_ID})
        msg = await g1.read_type('object')
        self.assertEqual(msg['object'], GENESIS_BLOCK)

    async def test_send_then_request_same_grader(self):
        g1 = await self.grader()
        await g1.send({'type': 'object', 'object': EXAMPLE_COINBASE})
        await g1.send({'type': 'getobject', 'objectid': EXAMPLE_COINBASE_ID})
        msg = await g1.read_type('object')
        self.assertEqual(msg, {'type': 'object', 'object': EXAMPLE_COINBASE})

    async def test_send_then_request_other_grader(self):
        g1, g2 = await self.grader(), await self.grader()
        await g1.send({'type': 'object', 'object': EXAMPLE_COINBASE})
        await g2.read_type('ihaveobject')
        await g2.send({'type': 'getobject', 'objectid': EXAMPLE_COINBASE_ID})
        msg = await g2.read_type('object')
        self.assertEqual(msg['object'], EXAMPLE_COINBASE)

    async def test_getobject_unknown_object(self):
        g1 = await self.grader()
        await g1.send({'type': 'getobject', 'objectid': 'ee' * 32})
        msg = await g1.read_type('error')
        self.assertEqual(msg['name'], 'UNKNOWN_OBJECT')
        self.assertTrue(msg['msg'])
        self.assertTrue(await g1.is_alive())  # connection stays open

    async def test_ihaveobject_new_object_is_requested(self):
        g1 = await self.grader()
        await g1.send({'type': 'ihaveobject', 'objectid': 'ee' * 32})
        msg = await g1.read_type('getobject')
        self.assertEqual(msg, {'type': 'getobject', 'objectid': 'ee' * 32})

    async def test_ihaveobject_known_object_is_not_requested(self):
        g1 = await self.grader()
        await g1.send({'type': 'ihaveobject', 'objectid': GENESIS_ID})
        await g1.expect_silence()

    async def test_invalid_objectid_closes_connection(self):
        g1 = await self.grader()
        await g1.send({'type': 'getobject', 'objectid': 'XYZ'})
        msg = await g1.read_type('error')
        self.assertEqual(msg['name'], 'INVALID_FORMAT')
        await g1.expect_closed()

    # ---------- Gossiping ----------

    async def test_valid_transaction_is_gossiped(self):
        g1, g2 = await self.grader(), await self.grader()
        await g1.send({'type': 'object', 'object': EXAMPLE_COINBASE})
        msg = await g2.read_type('ihaveobject')
        self.assertEqual(msg, {'type': 'ihaveobject', 'objectid': EXAMPLE_COINBASE_ID})
        await g1.send({'type': 'object', 'object': EXAMPLE_TX})
        msg = await g2.read_type('ihaveobject')
        self.assertEqual(msg['objectid'], EXAMPLE_TX_ID)

    async def test_gossip_reaches_all_peers(self):
        g1, g2, g3 = await self.grader(), await self.grader(), await self.grader()
        await g1.send({'type': 'object', 'object': EXAMPLE_COINBASE})
        for g in (g2, g3):
            self.assertEqual((await g.read_type('ihaveobject'))['objectid'], EXAMPLE_COINBASE_ID)

    async def test_known_object_is_not_gossiped_again(self):
        g1, g2 = await self.grader(), await self.grader()
        await g1.send({'type': 'object', 'object': EXAMPLE_COINBASE})
        await g2.read_type('ihaveobject')
        await g1.send({'type': 'object', 'object': EXAMPLE_COINBASE})
        await g2.expect_silence()
        self.assertTrue(await g1.is_alive())

    async def test_closed_peer_does_not_break_gossip(self):
        g1, g2, g3 = await self.grader(), await self.grader(), await self.grader()
        g3.close()
        await asyncio.sleep(0.1)
        await g1.send({'type': 'object', 'object': EXAMPLE_COINBASE})
        self.assertEqual((await g2.read_type('ihaveobject'))['objectid'], EXAMPLE_COINBASE_ID)

    # ---------- Transaction validation over the network ----------

    async def _expect_rejected(self, tx, error_name):
        g1, g2 = await self.grader(), await self.grader()
        await g1.send({'type': 'object', 'object': tx})
        msg = await g1.read_type('error')
        self.assertEqual(msg['name'], error_name, msg)
        await g1.expect_closed()
        await g2.expect_silence()  # not gossiped
        self.assertFalse(self.node.object_db.has(object_id(tx)))
        g2.close()

    async def _store(self, *objs):
        for obj in objs:
            self.node.object_db.put(obj)

    async def test_invalid_signature_rejected(self):
        await self._store(EXAMPLE_COINBASE)
        tx = json.loads(json.dumps(EXAMPLE_TX))
        tx['outputs'][0]['value'] = 11
        tx['outputs'][1]['value'] = 49999999999989
        await self._expect_rejected(tx, 'INVALID_TX_SIGNATURE')

    async def test_conservation_violation_rejected(self):
        alice, bob = Wallet(), Wallet()
        cb = coinbase(alice.pubkey, 100)
        await self._store(cb)
        await self._expect_rejected(
            spend([(object_id(cb), 0, alice)], [(bob.pubkey, 101)]), 'INVALID_TX_CONSERVATION'
        )

    async def test_double_input_rejected(self):
        alice = Wallet()
        cb = coinbase(alice.pubkey, 100)
        await self._store(cb)
        await self._expect_rejected(
            spend([(object_id(cb), 0, alice)] * 2, [(alice.pubkey, 1)]), 'INVALID_TX_CONSERVATION'
        )

    async def test_invalid_outpoint_index_rejected(self):
        alice = Wallet()
        cb = coinbase(alice.pubkey, 100)
        await self._store(cb)
        await self._expect_rejected(
            spend([(object_id(cb), 5, alice)], [(alice.pubkey, 1)]), 'INVALID_TX_OUTPOINT'
        )

    async def test_outpoint_to_block_rejected(self):
        alice = Wallet()
        await self._expect_rejected(
            spend([(GENESIS_ID, 0, alice)], [(alice.pubkey, 1)]), 'INVALID_FORMAT'
        )

    async def test_malformed_transactions_rejected(self):
        bad_txs = [
            {'type': 'transaction', 'outputs': []},  # no inputs, no height
            {'type': 'transaction', 'inputs': [], 'outputs': []},
            {'type': 'transaction', 'height': 0, 'outputs': [{'pubkey': 'ab' * 32, 'value': -1}]},
            {'type': 'transaction', 'height': 0, 'outputs': [{'pubkey': 'xx', 'value': 1}]},
            {'type': 'transaction', 'height': 0, 'outputs': [], 'extra': 1},
            {'type': 'transaction', 'height': 'zero', 'outputs': []},
        ]
        for tx in bad_txs:
            with self.subTest(tx=tx):
                await self._expect_rejected(tx, 'INVALID_FORMAT')

    async def test_unknown_object_type_rejected(self):
        await self._expect_rejected({'type': 'banana'}, 'INVALID_FORMAT')

    async def test_unknown_outpoint_keeps_connection_open(self):
        alice = Wallet()
        tx = spend([('ee' * 32, 0, alice)], [(alice.pubkey, 1)])
        g1, g2 = await self.grader(), await self.grader()
        await g1.send({'type': 'object', 'object': tx})
        msg = await g1.read_type('error')
        self.assertEqual(msg['name'], 'UNKNOWN_OBJECT')
        self.assertTrue(await g1.is_alive())
        await g2.expect_silence()
        self.assertFalse(self.node.object_db.has(object_id(tx)))

    async def test_spend_chain_is_accepted_and_gossiped(self):
        alice, bob = Wallet(), Wallet()
        cb = coinbase(alice.pubkey, 100)
        tx1 = spend([(object_id(cb), 0, alice)], [(bob.pubkey, 70), (alice.pubkey, 30)])
        tx2 = spend([(object_id(tx1), 0, bob)], [(alice.pubkey, 70)])
        g1, g2 = await self.grader(), await self.grader()
        for obj in (cb, tx1, tx2):
            await g1.send({'type': 'object', 'object': obj})
            self.assertEqual((await g2.read_type('ihaveobject'))['objectid'], object_id(obj))
        self.assertTrue(await g1.is_alive())

    async def test_block_object_is_invalid_format(self):
        block = dict(GENESIS_BLOCK, created=1671062401)
        await self._expect_rejected(block, 'INVALID_FORMAT')

    async def test_unhandled_message_types_still_ignored(self):
        g1 = await self.grader()
        for msg in ({'type': 'getchaintip'}, {'type': 'getmempool'},
                    {'type': 'chaintip', 'blockid': GENESIS_ID}, {'type': 'mempool', 'txids': []}):
            await g1.send(msg)
        self.assertTrue(await g1.is_alive())

    async def test_object_before_hello_is_invalid_handshake(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        g = Grader(reader, writer)
        self.graders.append(g)
        await g.send({'type': 'object', 'object': EXAMPLE_COINBASE})
        msg = await g.read_type('error')
        self.assertEqual(msg['name'], 'INVALID_HANDSHAKE')
        self.assertFalse(self.node.object_db.has(EXAMPLE_COINBASE_ID))

    # ---------- Persistence ----------

    async def test_objects_survive_node_restart(self):
        g1 = await self.grader()
        await g1.send({'type': 'object', 'object': EXAMPLE_COINBASE})
        await g1.send({'type': 'getobject', 'objectid': EXAMPLE_COINBASE_ID})
        await g1.read_type('object')
        await self.stop_node()
        await self.start_node()
        g2 = await self.grader()
        await g2.send({'type': 'getobject', 'objectid': EXAMPLE_COINBASE_ID})
        self.assertEqual((await g2.read_type('object'))['object'], EXAMPLE_COINBASE)


if __name__ == '__main__':
    unittest.main()
