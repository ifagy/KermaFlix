"""Tests for transaction syntax and semantic validation (objects.validate_transaction*)."""

import copy
import unittest

import helpers  # noqa: F401  (sets up the import path)
from helpers import (
    EXAMPLE_COINBASE, EXAMPLE_COINBASE_ID, EXAMPLE_TX, DictDB, Wallet, coinbase, spend,
)
from objects import (
    GENESIS_BLOCK, GENESIS_ID, KermaError, object_id, validate_transaction,
    validate_transaction_syntax,
)

PK = 'ab' * 32
TXID = 'cd' * 32
SIG = 'ef' * 64


def normal_tx():
    return {
        'type': 'transaction',
        'inputs': [{'outpoint': {'txid': TXID, 'index': 0}, 'sig': SIG}],
        'outputs': [{'pubkey': PK, 'value': 1}],
    }


class TransactionSyntaxTest(unittest.TestCase):
    def assertInvalidFormat(self, tx):
        with self.assertRaises(KermaError) as ctx:
            validate_transaction_syntax(tx)
        self.assertEqual(ctx.exception.name, 'INVALID_FORMAT', ctx.exception.msg)

    def mutate(self, fn):
        tx = normal_tx()
        fn(tx)
        self.assertInvalidFormat(tx)

    # --- valid ---

    def test_valid_normal_transaction(self):
        validate_transaction_syntax(normal_tx())

    def test_valid_coinbase(self):
        validate_transaction_syntax(EXAMPLE_COINBASE)

    def test_valid_example_transaction(self):
        validate_transaction_syntax(EXAMPLE_TX)

    def test_empty_outputs_allowed(self):
        self.mutate_ok(lambda tx: tx.update(outputs=[]))

    def test_zero_value_allowed(self):
        self.mutate_ok(lambda tx: tx['outputs'][0].update(value=0))

    def mutate_ok(self, fn):
        tx = normal_tx()
        fn(tx)
        validate_transaction_syntax(tx)

    # --- general structure ---

    def test_not_a_dict(self):
        self.assertInvalidFormat([])

    def test_wrong_type(self):
        self.mutate(lambda tx: tx.update(type='block'))

    def test_additional_key(self):
        self.mutate(lambda tx: tx.update(extra=1))

    def test_missing_outputs(self):
        self.mutate(lambda tx: tx.pop('outputs'))

    def test_outputs_not_a_list(self):
        self.mutate(lambda tx: tx.update(outputs={}))

    # --- inputs ---

    def test_missing_inputs(self):
        self.mutate(lambda tx: tx.pop('inputs'))

    def test_empty_inputs(self):
        self.mutate(lambda tx: tx.update(inputs=[]))

    def test_inputs_not_a_list(self):
        self.mutate(lambda tx: tx.update(inputs='x'))

    def test_input_not_a_dict(self):
        self.mutate(lambda tx: tx.update(inputs=[1]))

    def test_input_missing_sig(self):
        self.mutate(lambda tx: tx['inputs'][0].pop('sig'))

    def test_input_null_sig(self):
        self.mutate(lambda tx: tx['inputs'][0].update(sig=None))

    def test_input_short_sig(self):
        self.mutate(lambda tx: tx['inputs'][0].update(sig='ef' * 32))

    def test_input_upper_case_sig(self):
        self.mutate(lambda tx: tx['inputs'][0].update(sig='EF' * 64))

    def test_input_additional_key(self):
        self.mutate(lambda tx: tx['inputs'][0].update(extra=1))

    def test_input_missing_outpoint(self):
        self.mutate(lambda tx: tx['inputs'][0].pop('outpoint'))

    def test_outpoint_not_a_dict(self):
        self.mutate(lambda tx: tx['inputs'][0].update(outpoint=TXID))

    def test_outpoint_missing_txid(self):
        self.mutate(lambda tx: tx['inputs'][0]['outpoint'].pop('txid'))

    def test_outpoint_missing_index(self):
        self.mutate(lambda tx: tx['inputs'][0]['outpoint'].pop('index'))

    def test_outpoint_additional_key(self):
        self.mutate(lambda tx: tx['inputs'][0]['outpoint'].update(extra=1))

    def test_outpoint_invalid_txid(self):
        for bad in ('xyz', 'CD' * 32, 'cd' * 31, 123, None):
            self.mutate(lambda tx: tx['inputs'][0]['outpoint'].update(txid=bad))

    def test_outpoint_invalid_index(self):
        for bad in (-1, 1.5, '0', None, True):
            self.mutate(lambda tx: tx['inputs'][0]['outpoint'].update(index=bad))

    # --- outputs ---

    def test_output_not_a_dict(self):
        self.mutate(lambda tx: tx.update(outputs=['x']))

    def test_output_missing_pubkey(self):
        self.mutate(lambda tx: tx['outputs'][0].pop('pubkey'))

    def test_output_missing_value(self):
        self.mutate(lambda tx: tx['outputs'][0].pop('value'))

    def test_output_additional_key(self):
        self.mutate(lambda tx: tx['outputs'][0].update(extra=1))

    def test_output_invalid_pubkey(self):
        for bad in ('ab' * 31, 'AB' * 32, 'zz' * 32, 5, None):
            self.mutate(lambda tx: tx['outputs'][0].update(pubkey=bad))

    def test_output_negative_value(self):
        self.mutate(lambda tx: tx['outputs'][0].update(value=-1))

    def test_output_invalid_value_type(self):
        for bad in (1.5, 1.0, '1', None, True):
            self.mutate(lambda tx: tx['outputs'][0].update(value=bad))

    # --- coinbase ---

    def test_coinbase_with_inputs(self):
        tx = copy.deepcopy(EXAMPLE_COINBASE)
        tx['inputs'] = normal_tx()['inputs']
        self.assertInvalidFormat(tx)

    def test_coinbase_invalid_height(self):
        for bad in (-1, '1', 1.5, None, False):
            tx = copy.deepcopy(EXAMPLE_COINBASE)
            tx['height'] = bad
            self.assertInvalidFormat(tx)

    def test_coinbase_additional_key(self):
        tx = copy.deepcopy(EXAMPLE_COINBASE)
        tx['note'] = 'x'
        self.assertInvalidFormat(tx)

    def test_coinbase_invalid_output(self):
        tx = copy.deepcopy(EXAMPLE_COINBASE)
        tx['outputs'][0]['value'] = -5
        self.assertInvalidFormat(tx)


class TransactionValidationTest(unittest.TestCase):
    def setUp(self):
        self.alice = Wallet()
        self.bob = Wallet()
        self.cb = coinbase(self.alice.pubkey, value=100)
        self.cb_id = object_id(self.cb)
        self.db = DictDB(GENESIS_BLOCK, self.cb)

    def assertError(self, tx, name, db=None):
        with self.assertRaises(KermaError) as ctx:
            validate_transaction(tx, db or self.db)
        self.assertEqual(ctx.exception.name, name, ctx.exception.msg)
        return ctx.exception

    # --- valid ---

    def test_example_transactions_are_valid(self):
        validate_transaction(EXAMPLE_COINBASE, DictDB())
        validate_transaction(EXAMPLE_TX, DictDB(EXAMPLE_COINBASE))

    def test_coinbase_always_valid(self):
        validate_transaction(coinbase(self.bob.pubkey, value=10 ** 20, height=99), self.db)

    def test_valid_spend(self):
        validate_transaction(spend([(self.cb_id, 0, self.alice)], [(self.bob.pubkey, 60)]), self.db)

    def test_spend_exact_amount(self):
        validate_transaction(spend([(self.cb_id, 0, self.alice)], [(self.bob.pubkey, 100)]), self.db)

    def test_spend_with_fee_and_multiple_outputs(self):
        tx = spend([(self.cb_id, 0, self.alice)], [(self.bob.pubkey, 30), (self.alice.pubkey, 50)])
        validate_transaction(tx, self.db)

    def test_spend_without_outputs(self):
        validate_transaction(spend([(self.cb_id, 0, self.alice)], []), self.db)

    def test_spend_from_non_coinbase_transaction(self):
        tx1 = spend([(self.cb_id, 0, self.alice)], [(self.bob.pubkey, 40), (self.alice.pubkey, 60)])
        self.db.data[object_id(tx1)] = tx1
        tx2 = spend([(object_id(tx1), 1, self.alice)], [(self.bob.pubkey, 60)])
        validate_transaction(tx2, self.db)

    def test_multiple_inputs_with_different_keys(self):
        cb2 = coinbase(self.bob.pubkey, value=50, height=1)
        self.db.data[object_id(cb2)] = cb2
        tx = spend(
            [(self.cb_id, 0, self.alice), (object_id(cb2), 0, self.bob)],
            [(self.bob.pubkey, 150)],
        )
        validate_transaction(tx, self.db)

    def test_same_output_spent_by_different_transactions(self):
        # Double spends across transactions are only resolved in blocks.
        tx_a = spend([(self.cb_id, 0, self.alice)], [(self.bob.pubkey, 10)])
        tx_b = spend([(self.cb_id, 0, self.alice)], [(self.alice.pubkey, 20)])
        validate_transaction(tx_a, self.db)
        validate_transaction(tx_b, self.db)

    # --- invalid ---

    def test_invalid_syntax(self):
        tx = spend([(self.cb_id, 0, self.alice)], [(self.bob.pubkey, 10)])
        tx['outputs'][0]['value'] = -1
        self.assertError(tx, 'INVALID_FORMAT')

    def test_unknown_outpoint_transaction(self):
        tx = spend([('ee' * 32, 0, self.alice)], [(self.bob.pubkey, 10)])
        e = self.assertError(tx, 'UNKNOWN_OBJECT')
        self.assertFalse(e.fatal)

    def test_outpoint_references_block(self):
        tx = spend([(GENESIS_ID, 0, self.alice)], [(self.bob.pubkey, 10)])
        self.assertError(tx, 'INVALID_FORMAT')

    def test_outpoint_index_too_high(self):
        tx = spend([(self.cb_id, 1, self.alice)], [(self.bob.pubkey, 10)])
        self.assertError(tx, 'INVALID_TX_OUTPOINT')

    def test_signature_by_wrong_key(self):
        tx = spend([(self.cb_id, 0, self.bob)], [(self.bob.pubkey, 10)])
        self.assertError(tx, 'INVALID_TX_SIGNATURE')

    def test_tampered_output_after_signing(self):
        tx = spend([(self.cb_id, 0, self.alice)], [(self.bob.pubkey, 10)])
        tx['outputs'][0]['value'] = 11
        self.assertError(tx, 'INVALID_TX_SIGNATURE')

    def test_tampered_signature(self):
        tx = spend([(self.cb_id, 0, self.alice)], [(self.bob.pubkey, 10)])
        sig = tx['inputs'][0]['sig']
        tx['inputs'][0]['sig'] = ('0' if sig[0] != '0' else '1') + sig[1:]
        self.assertError(tx, 'INVALID_TX_SIGNATURE')

    def test_one_of_two_signatures_invalid(self):
        cb2 = coinbase(self.bob.pubkey, value=50, height=1)
        self.db.data[object_id(cb2)] = cb2
        tx = spend(
            [(self.cb_id, 0, self.alice), (object_id(cb2), 0, self.alice)],
            [(self.bob.pubkey, 10)],
        )
        self.assertError(tx, 'INVALID_TX_SIGNATURE')

    def test_example_signature_on_modified_tx(self):
        tx = copy.deepcopy(EXAMPLE_TX)
        tx['outputs'][0]['value'] = 11
        tx['outputs'][1]['value'] = 49999999999989
        self.assertError(tx, 'INVALID_TX_SIGNATURE', DictDB(EXAMPLE_COINBASE))

    def test_outputs_exceed_inputs(self):
        tx = spend([(self.cb_id, 0, self.alice)], [(self.bob.pubkey, 101)])
        self.assertError(tx, 'INVALID_TX_CONSERVATION')

    def test_outputs_exceed_inputs_with_multiple_outputs(self):
        tx = spend([(self.cb_id, 0, self.alice)], [(self.bob.pubkey, 60), (self.bob.pubkey, 41)])
        self.assertError(tx, 'INVALID_TX_CONSERVATION')

    def test_same_outpoint_twice_in_one_transaction(self):
        tx = spend(
            [(self.cb_id, 0, self.alice), (self.cb_id, 0, self.alice)],
            [(self.bob.pubkey, 150)],
        )
        self.assertError(tx, 'INVALID_TX_CONSERVATION')

    def test_same_outpoint_twice_even_if_amount_fits(self):
        tx = spend(
            [(self.cb_id, 0, self.alice), (self.cb_id, 0, self.alice)],
            [(self.bob.pubkey, 10)],
        )
        self.assertError(tx, 'INVALID_TX_CONSERVATION')


if __name__ == '__main__':
    unittest.main()
