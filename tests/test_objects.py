"""Tests for canonical JSON, object ids, hex helpers and signatures (objects.py)."""

import copy
import unittest

import helpers  # noqa: F401  (sets up the import path)
from helpers import (
    EXAMPLE_COINBASE, EXAMPLE_COINBASE_ID, EXAMPLE_TX, EXAMPLE_TX_ID, Wallet,
)
from objects import (
    GENESIS_BLOCK, GENESIS_ID, KermaError, canonical_json, is_hash,
    is_non_negative_int, is_signature, object_id, signing_payload, verify_signature,
)


class CanonicalJsonTest(unittest.TestCase):
    def test_sorted_keys_and_no_whitespace(self):
        self.assertEqual(canonical_json({'b': 1, 'a': [1, 2]}), b'{"a":[1,2],"b":1}')

    def test_nested_objects_are_sorted(self):
        self.assertEqual(
            canonical_json({'z': {'y': 1, 'x': 2}, 'a': None}),
            b'{"a":null,"z":{"x":2,"y":1}}',
        )

    def test_no_trailing_newline(self):
        self.assertFalse(canonical_json({'a': 1}).endswith(b'\n'))

    def test_key_order_does_not_change_encoding(self):
        a = {'type': 'transaction', 'height': 1, 'outputs': []}
        b = {'outputs': [], 'height': 1, 'type': 'transaction'}
        self.assertEqual(canonical_json(a), canonical_json(b))

    def test_large_integers_are_exact(self):
        self.assertEqual(canonical_json({'v': 50000000000000}), b'{"v":50000000000000}')


class ObjectIdTest(unittest.TestCase):
    def test_genesis_block_id(self):
        self.assertEqual(object_id(GENESIS_BLOCK), GENESIS_ID)

    def test_example_coinbase_id(self):
        self.assertEqual(object_id(EXAMPLE_COINBASE), EXAMPLE_COINBASE_ID)

    def test_example_transaction_id(self):
        self.assertEqual(object_id(EXAMPLE_TX), EXAMPLE_TX_ID)

    def test_id_is_lower_case_hex_of_32_bytes(self):
        self.assertTrue(is_hash(object_id({'type': 'x'})))

    def test_id_independent_of_key_order(self):
        reordered = dict(reversed(list(EXAMPLE_COINBASE.items())))
        self.assertEqual(object_id(reordered), EXAMPLE_COINBASE_ID)

    def test_different_objects_have_different_ids(self):
        changed = copy.deepcopy(EXAMPLE_COINBASE)
        changed['height'] = 1
        self.assertNotEqual(object_id(changed), EXAMPLE_COINBASE_ID)


class HelperPredicateTest(unittest.TestCase):
    def test_is_hash(self):
        self.assertTrue(is_hash('a' * 64))
        self.assertTrue(is_hash('0123456789abcdef' * 4))
        self.assertFalse(is_hash('A' * 64))  # upper case
        self.assertFalse(is_hash('a' * 63))
        self.assertFalse(is_hash('a' * 65))
        self.assertFalse(is_hash('g' * 64))
        self.assertFalse(is_hash(None))
        self.assertFalse(is_hash(123))

    def test_is_signature(self):
        self.assertTrue(is_signature('f' * 128))
        self.assertFalse(is_signature('f' * 64))
        self.assertFalse(is_signature('F' * 128))
        self.assertFalse(is_signature(None))

    def test_is_non_negative_int(self):
        self.assertTrue(is_non_negative_int(0))
        self.assertTrue(is_non_negative_int(10 ** 30))
        self.assertFalse(is_non_negative_int(-1))
        self.assertFalse(is_non_negative_int(1.0))
        self.assertFalse(is_non_negative_int('1'))
        self.assertFalse(is_non_negative_int(True))
        self.assertFalse(is_non_negative_int(None))


class KermaErrorTest(unittest.TestCase):
    def test_fatal_errors(self):
        for name in ('INVALID_FORMAT', 'INVALID_TX_SIGNATURE', 'INVALID_TX_OUTPOINT',
                     'INVALID_TX_CONSERVATION'):
            self.assertTrue(KermaError(name, 'x').fatal, name)

    def test_non_fatal_errors(self):
        self.assertFalse(KermaError('UNKNOWN_OBJECT', 'x').fatal)
        self.assertFalse(KermaError('UNFINDABLE_OBJECT', 'x').fatal)

    def test_attributes(self):
        e = KermaError('INVALID_FORMAT', 'details')
        self.assertEqual((e.name, e.msg), ('INVALID_FORMAT', 'details'))


class SignatureTest(unittest.TestCase):
    def test_signing_payload_nulls_signatures(self):
        payload = signing_payload(EXAMPLE_TX)
        self.assertIn(b'"sig":null', payload)
        self.assertNotIn(EXAMPLE_TX['inputs'][0]['sig'].encode(), payload)

    def test_signing_payload_does_not_modify_tx(self):
        tx = copy.deepcopy(EXAMPLE_TX)
        signing_payload(tx)
        self.assertEqual(tx, EXAMPLE_TX)

    def test_example_signature_verifies(self):
        self.assertTrue(verify_signature(
            EXAMPLE_COINBASE['outputs'][0]['pubkey'],
            EXAMPLE_TX['inputs'][0]['sig'],
            signing_payload(EXAMPLE_TX),
        ))

    def test_generated_signature_verifies(self):
        w = Wallet()
        self.assertTrue(verify_signature(w.pubkey, w.sign(b'hello'), b'hello'))

    def test_wrong_message_fails(self):
        w = Wallet()
        self.assertFalse(verify_signature(w.pubkey, w.sign(b'hello'), b'hellO'))

    def test_wrong_key_fails(self):
        w1, w2 = Wallet(), Wallet()
        self.assertFalse(verify_signature(w2.pubkey, w1.sign(b'msg'), b'msg'))

    def test_garbage_signature_fails(self):
        w = Wallet()
        self.assertFalse(verify_signature(w.pubkey, '00' * 64, b'msg'))


if __name__ == '__main__':
    unittest.main()
