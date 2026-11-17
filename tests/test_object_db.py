"""Tests for the persistent object database (object_db.py)."""

import os
import tempfile
import unittest

import helpers  # noqa: F401  (sets up the import path)
from helpers import (
    EXAMPLE_BLOCK, EXAMPLE_BLOCK_ID, EXAMPLE_CB_H1_ID, EXAMPLE_COINBASE, EXAMPLE_COINBASE_ID,
    EXAMPLE_TX, EXAMPLE_TX_ID,
)
from object_db import ObjectDB
from objects import GENESIS_BLOCK, GENESIS_ID


class ObjectDBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = os.path.join(self.tmp.name, 'sub', 'objects.db')
        self.db = ObjectDB(self.path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_new_database_contains_only_genesis(self):
        self.assertEqual(self.db.count(), 1)
        self.assertTrue(self.db.has(GENESIS_ID))
        self.assertEqual(self.db.get(GENESIS_ID), GENESIS_BLOCK)

    def test_creates_missing_directory(self):
        self.assertTrue(os.path.exists(self.path))

    def test_unknown_object(self):
        self.assertFalse(self.db.has(EXAMPLE_COINBASE_ID))
        self.assertIsNone(self.db.get(EXAMPLE_COINBASE_ID))

    def test_put_returns_object_id(self):
        self.assertEqual(self.db.put(EXAMPLE_COINBASE), EXAMPLE_COINBASE_ID)

    def test_put_and_get(self):
        self.db.put(EXAMPLE_COINBASE)
        self.db.put(EXAMPLE_TX)
        self.assertTrue(self.db.has(EXAMPLE_COINBASE_ID))
        self.assertEqual(self.db.get(EXAMPLE_COINBASE_ID), EXAMPLE_COINBASE)
        self.assertEqual(self.db.get(EXAMPLE_TX_ID), EXAMPLE_TX)
        self.assertEqual(self.db.count(), 3)

    def test_put_is_idempotent(self):
        self.db.put(EXAMPLE_COINBASE)
        self.db.put(EXAMPLE_COINBASE)
        self.assertEqual(self.db.count(), 2)

    def test_objects_survive_restart(self):
        self.db.put(EXAMPLE_COINBASE)
        self.db.close()
        self.db = ObjectDB(self.path)
        self.assertEqual(self.db.get(EXAMPLE_COINBASE_ID), EXAMPLE_COINBASE)
        self.assertEqual(self.db.count(), 2)  # genesis not duplicated

    # ---------- Block state (Task 3) ----------

    def test_genesis_state(self):
        self.assertEqual(self.db.get_block_state(GENESIS_ID), (0, set()))

    def test_no_state_for_transactions_or_unknown(self):
        self.db.put(EXAMPLE_COINBASE)
        self.assertIsNone(self.db.get_block_state(EXAMPLE_COINBASE_ID))
        self.assertIsNone(self.db.get_block_state('ee' * 32))

    def test_put_block_stores_object_and_state(self):
        utxo = {(EXAMPLE_CB_H1_ID, 0), ('ab' * 32, 3)}
        oid = self.db.put_block(EXAMPLE_BLOCK, 1, utxo)
        self.assertEqual(oid, EXAMPLE_BLOCK_ID)
        self.assertEqual(self.db.get(EXAMPLE_BLOCK_ID), EXAMPLE_BLOCK)
        self.assertEqual(self.db.get_block_state(EXAMPLE_BLOCK_ID), (1, utxo))

    def test_block_state_survives_restart(self):
        self.db.put_block(EXAMPLE_BLOCK, 1, {(EXAMPLE_CB_H1_ID, 0)})
        self.db.close()
        self.db = ObjectDB(self.path)
        self.assertEqual(self.db.get_block_state(EXAMPLE_BLOCK_ID), (1, {(EXAMPLE_CB_H1_ID, 0)}))
        self.assertEqual(self.db.get_block_state(GENESIS_ID), (0, set()))


if __name__ == '__main__':
    unittest.main()
