"""Tests for the persistent object database (object_db.py)."""

import os
import tempfile
import unittest

import helpers  # noqa: F401  (sets up the import path)
from helpers import EXAMPLE_COINBASE, EXAMPLE_COINBASE_ID, EXAMPLE_TX, EXAMPLE_TX_ID
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


if __name__ == '__main__':
    unittest.main()
