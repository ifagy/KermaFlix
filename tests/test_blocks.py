"""Tests for block syntax, target, proof-of-work, genesis rule and block execution (blocks.py)."""

import copy
import unittest

import helpers  # noqa: F401  (sets up the import path)
from helpers import (
    BLOCK_REWARD, EXAMPLE_BLOCK, EXAMPLE_BLOCK_ID, EXAMPLE_CB_H1, EXAMPLE_CB_H1_ID,
    GENESIS_CREATED, DictDB, Wallet, coinbase, make_block, mine, spend, unmine,
)
from blocks import (
    TARGET, execute_block, has_valid_pow, validate_block_header, validate_block_syntax,
)
from objects import GENESIS_BLOCK, GENESIS_ID, KermaError, object_id

NOW = GENESIS_CREATED + 10 ** 6


def valid_block():
    return copy.deepcopy(EXAMPLE_BLOCK)


class BlockSyntaxTest(unittest.TestCase):
    def assertInvalidFormat(self, block):
        with self.assertRaises(KermaError) as ctx:
            validate_block_syntax(block)
        self.assertEqual(ctx.exception.name, 'INVALID_FORMAT', ctx.exception.msg)

    def mutate(self, fn):
        block = valid_block()
        fn(block)
        self.assertInvalidFormat(block)

    def test_example_block_valid(self):
        validate_block_syntax(valid_block())

    def test_genesis_valid(self):
        validate_block_syntax(GENESIS_BLOCK)

    def test_optional_keys_may_be_missing(self):
        block = valid_block()
        del block['miner'], block['note']
        validate_block_syntax(block)

    def test_empty_strings_and_max_length_allowed(self):
        block = valid_block()
        block['miner'] = ''
        block['note'] = 'x' * 128
        validate_block_syntax(block)

    def test_not_a_dict(self):
        self.assertInvalidFormat(['block'])

    def test_wrong_type(self):
        self.mutate(lambda b: b.update(type='transaction'))

    def test_missing_required_keys(self):
        for key in ('txids', 'nonce', 'previd', 'created', 'T'):
            with self.subTest(key=key):
                self.mutate(lambda b: b.pop(key))

    def test_additional_key(self):
        self.mutate(lambda b: b.update(extra='x'))

    def test_invalid_txids(self):
        for bad in ('ab' * 32, None, [1], ['AB' * 32], ['ab' * 31], [None]):
            with self.subTest(txids=bad):
                self.mutate(lambda b: b.update(txids=bad))

    def test_invalid_nonce(self):
        for bad in ('ab' * 31, 'AB' * 32, 'zz' * 32, 123, None):
            with self.subTest(nonce=bad):
                self.mutate(lambda b: b.update(nonce=bad))

    def test_invalid_previd(self):
        for bad in ('ab' * 31, 'AB' * 32, 5, ''):
            with self.subTest(previd=bad):
                self.mutate(lambda b: b.update(previd=bad))

    def test_invalid_created(self):
        for bad in ('1671148800', 1671148800.5, -1, None, True):
            with self.subTest(created=bad):
                self.mutate(lambda b: b.update(created=bad))

    def test_invalid_target_format(self):
        for bad in ('0000abc', 5, None, TARGET.upper()):
            with self.subTest(T=bad):
                self.mutate(lambda b: b.update(T=bad))

    def test_invalid_miner_and_note(self):
        for key in ('miner', 'note'):
            for bad in (5, None, 'x' * 129, 'bad\nnewline', 'non-ascii \u00e9'):
                with self.subTest(key=key, value=bad):
                    self.mutate(lambda b: b.update({key: bad}))


class BlockHeaderTest(unittest.TestCase):
    def assertHeaderError(self, block, name):
        with self.assertRaises(KermaError) as ctx:
            validate_block_header(block, object_id(block))
        self.assertEqual(ctx.exception.name, name, ctx.exception.msg)

    def test_example_block_ids(self):
        self.assertEqual(object_id(EXAMPLE_CB_H1), EXAMPLE_CB_H1_ID)
        self.assertEqual(object_id(EXAMPLE_BLOCK), EXAMPLE_BLOCK_ID)

    def test_example_and_genesis_pass(self):
        validate_block_header(EXAMPLE_BLOCK, EXAMPLE_BLOCK_ID)
        validate_block_header(GENESIS_BLOCK, GENESIS_ID)

    def test_has_valid_pow(self):
        self.assertTrue(has_valid_pow(EXAMPLE_BLOCK_ID))
        self.assertTrue(has_valid_pow('0000abbf' + 'f' * 56))
        self.assertFalse(has_valid_pow(TARGET))  # must be strictly less
        self.assertFalse(has_valid_pow('0000abc1' + '0' * 56))
        self.assertFalse(has_valid_pow('f' * 64))

    def test_wrong_target(self):
        block = mine(dict(valid_block(), T='0000abd' + '0' * 57))
        self.assertHeaderError(block, 'INVALID_FORMAT')

    def test_easier_target(self):
        block = dict(valid_block(), T='f' * 64)
        self.assertHeaderError(block, 'INVALID_FORMAT')

    def test_invalid_pow(self):
        self.assertHeaderError(unmine(valid_block()), 'INVALID_BLOCK_POW')

    def test_tampered_block_fails_pow(self):
        block = valid_block()
        block['note'] = 'tampered'
        self.assertHeaderError(block, 'INVALID_BLOCK_POW')

    def test_mined_block_passes(self):
        block = make_block()
        validate_block_header(block, object_id(block))

    def test_null_previd_not_genesis(self):
        block = make_block(previd=None)
        self.assertHeaderError(block, 'INVALID_GENESIS')

    def test_syntax_checked_first(self):
        block = valid_block()
        block['extra'] = 1
        self.assertHeaderError(block, 'INVALID_FORMAT')


class ExecuteBlockTest(unittest.TestCase):
    """Block execution against a parent (no proof-of-work needed here)."""

    def setUp(self):
        self.alice = Wallet()
        self.bob = Wallet()
        # Parent: block at height 1 whose UTXO set holds alice's coinbase (100 picaker).
        self.cb1 = coinbase(self.alice.pubkey, value=100, height=1)
        self.cb1_id = object_id(self.cb1)
        self.parent = make_block([self.cb1_id], do_mine=False)
        self.parent_height = 1
        self.parent_utxo = {(self.cb1_id, 0)}
        self.db = DictDB(GENESIS_BLOCK, self.cb1)

    def block(self, txs, created=None):
        for tx in txs:
            self.db.add(tx)
        return make_block(
            [object_id(t) for t in txs], previd=object_id(self.parent),
            created=self.parent['created'] + 1 if created is None else created,
            do_mine=False,
        )

    def run_block(self, txs, created=None, now=NOW):
        block = self.block(txs, created)
        return execute_block(
            block, self.parent, self.parent_height, self.parent_utxo,
            [self.db.get(t) for t in block['txids']], self.db.get, now,
        )

    def assertError(self, txs, name, **kwargs):
        with self.assertRaises(KermaError) as ctx:
            self.run_block(txs, **kwargs)
        self.assertEqual(ctx.exception.name, name, ctx.exception.msg)

    # --- valid ---

    def test_empty_block(self):
        height, utxo = self.run_block([])
        self.assertEqual(height, 2)
        self.assertEqual(utxo, self.parent_utxo)

    def test_genesis_child_example(self):
        height, utxo = execute_block(
            EXAMPLE_BLOCK, GENESIS_BLOCK, 0, set(), [EXAMPLE_CB_H1], DictDB().get, NOW,
        )
        self.assertEqual(height, 1)
        self.assertEqual(utxo, {(EXAMPLE_CB_H1_ID, 0)})

    def test_coinbase_adds_output(self):
        cb = coinbase(self.bob.pubkey, BLOCK_REWARD, height=2)
        _, utxo = self.run_block([cb])
        self.assertEqual(utxo, {(self.cb1_id, 0), (object_id(cb), 0)})

    def test_coinbase_below_reward_allowed(self):
        self.run_block([coinbase(self.bob.pubkey, 0, height=2)])

    def test_spend_updates_utxo(self):
        tx = spend([(self.cb1_id, 0, self.alice)], [(self.bob.pubkey, 60), (self.alice.pubkey, 30)])
        _, utxo = self.run_block([tx])
        tid = object_id(tx)
        self.assertEqual(utxo, {(tid, 0), (tid, 1)})

    def test_parent_utxo_not_modified(self):
        tx = spend([(self.cb1_id, 0, self.alice)], [(self.bob.pubkey, 60)])
        self.run_block([tx])
        self.assertEqual(self.parent_utxo, {(self.cb1_id, 0)})

    def test_spend_from_earlier_tx_in_same_block(self):
        tx1 = spend([(self.cb1_id, 0, self.alice)], [(self.bob.pubkey, 100)])
        tx2 = spend([(object_id(tx1), 0, self.bob)], [(self.alice.pubkey, 100)])
        _, utxo = self.run_block([tx1, tx2])
        self.assertEqual(utxo, {(object_id(tx2), 0)})

    def test_coinbase_collects_fees(self):
        tx = spend([(self.cb1_id, 0, self.alice)], [(self.bob.pubkey, 60)])  # fee 40
        cb = coinbase(self.bob.pubkey, BLOCK_REWARD + 40, height=2)
        _, utxo = self.run_block([cb, tx])
        self.assertIn((object_id(cb), 0), utxo)

    def test_created_equal_to_now_allowed(self):
        self.run_block([], created=NOW, now=NOW)

    # --- timestamps ---

    def test_timestamp_equal_to_parent(self):
        self.assertError([], 'INVALID_BLOCK_TIMESTAMP', created=self.parent['created'])

    def test_timestamp_before_parent(self):
        self.assertError([], 'INVALID_BLOCK_TIMESTAMP', created=self.parent['created'] - 5)

    def test_timestamp_in_future(self):
        self.assertError([], 'INVALID_BLOCK_TIMESTAMP', created=NOW + 1)

    # --- references ---

    def test_parent_is_not_a_block(self):
        block = make_block([], previd=self.cb1_id, do_mine=False)
        with self.assertRaises(KermaError) as ctx:
            execute_block(block, self.cb1, 0, set(), [], self.db.get, NOW)
        self.assertEqual(ctx.exception.name, 'INVALID_FORMAT')

    def test_txid_refers_to_block(self):
        block = make_block([GENESIS_ID], previd=object_id(self.parent), created=self.parent['created'] + 1,
                           do_mine=False)
        with self.assertRaises(KermaError) as ctx:
            execute_block(block, self.parent, 1, self.parent_utxo, [GENESIS_BLOCK], self.db.get, NOW)
        self.assertEqual(ctx.exception.name, 'INVALID_FORMAT')

    # --- UTXO ---

    def test_spend_output_not_in_utxo(self):
        other = coinbase(self.alice.pubkey, 100, height=7)  # valid tx, but not in the chain
        self.db.add(other)
        tx = spend([(object_id(other), 0, self.alice)], [(self.bob.pubkey, 1)])
        self.assertError([tx], 'INVALID_TX_OUTPOINT')

    def test_double_spend_in_block(self):
        tx1 = spend([(self.cb1_id, 0, self.alice)], [(self.bob.pubkey, 10)])
        tx2 = spend([(self.cb1_id, 0, self.alice)], [(self.alice.pubkey, 20)])
        self.assertError([tx1, tx2], 'INVALID_TX_OUTPOINT')

    def test_same_tx_twice_in_block(self):
        tx = spend([(self.cb1_id, 0, self.alice)], [(self.bob.pubkey, 10)])
        self.assertError([tx, tx], 'INVALID_TX_OUTPOINT')

    def test_spend_coinbase_of_same_block(self):
        cb = coinbase(self.alice.pubkey, 10, height=2)
        tx = spend([(object_id(cb), 0, self.alice)], [(self.bob.pubkey, 1)])
        self.assertError([cb, tx], 'INVALID_TX_OUTPOINT')

    def test_spend_later_output_in_same_block(self):
        tx1 = spend([(self.cb1_id, 0, self.alice)], [(self.bob.pubkey, 100)])
        tx2 = spend([(object_id(tx1), 0, self.bob)], [(self.alice.pubkey, 100)])
        self.assertError([tx2, tx1], 'INVALID_TX_OUTPOINT')

    # --- coinbase ---

    def test_two_coinbases(self):
        self.assertError(
            [coinbase(self.bob.pubkey, 1, height=2), coinbase(self.alice.pubkey, 1, height=2)],
            'INVALID_BLOCK_COINBASE',
        )

    def test_coinbase_not_first(self):
        tx = spend([(self.cb1_id, 0, self.alice)], [(self.bob.pubkey, 10)])
        self.assertError([tx, coinbase(self.bob.pubkey, 1, height=2)], 'INVALID_BLOCK_COINBASE')

    def test_coinbase_wrong_height(self):
        for h in (0, 1, 3):
            with self.subTest(height=h):
                self.assertError([coinbase(self.bob.pubkey, 1, height=h)], 'INVALID_BLOCK_COINBASE')

    def test_coinbase_two_outputs(self):
        cb = coinbase(self.bob.pubkey, 1, height=2)
        cb['outputs'].append({'pubkey': self.alice.pubkey, 'value': 1})
        self.assertError([cb], 'INVALID_BLOCK_COINBASE')

    def test_coinbase_no_outputs(self):
        cb = coinbase(self.bob.pubkey, 1, height=2)
        cb['outputs'] = []
        self.assertError([cb], 'INVALID_BLOCK_COINBASE')

    def test_coinbase_exceeds_reward(self):
        self.assertError([coinbase(self.bob.pubkey, BLOCK_REWARD + 1, height=2)], 'INVALID_BLOCK_COINBASE')

    def test_coinbase_exceeds_reward_plus_fees(self):
        tx = spend([(self.cb1_id, 0, self.alice)], [(self.bob.pubkey, 60)])  # fee 40
        cb = coinbase(self.bob.pubkey, BLOCK_REWARD + 41, height=2)
        self.assertError([cb, tx], 'INVALID_BLOCK_COINBASE')


if __name__ == '__main__':
    unittest.main()
