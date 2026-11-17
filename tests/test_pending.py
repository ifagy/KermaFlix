"""Tests for waiting on pending objects (pending.py)."""

import asyncio
import unittest

import helpers  # noqa: F401  (sets up the import path)
from objects import KermaError
from pending import INVALID, UNFINDABLE, VALID, PendingObjects

A, B = 'aa' * 32, 'bb' * 32


class PendingObjectsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.p = PendingObjects()

    async def test_in_progress_tracking(self):
        self.assertFalse(self.p.in_progress(A))
        self.p.start(A)
        self.assertTrue(self.p.in_progress(A))
        self.p.resolve(A, VALID)
        self.assertFalse(self.p.in_progress(A))

    async def test_wait_all_succeeds_when_all_valid(self):
        task = asyncio.ensure_future(self.p.wait_all([A, B], 2))
        await asyncio.sleep(0)
        self.p.resolve(A, VALID)
        await asyncio.sleep(0)
        self.assertFalse(task.done())  # still waiting for B
        self.p.resolve(B, VALID)
        await asyncio.wait_for(task, 1)

    async def test_invalid_gives_invalid_ancestry(self):
        task = asyncio.ensure_future(self.p.wait_all([A, B], 2))
        await asyncio.sleep(0)
        self.p.resolve(B, INVALID)
        with self.assertRaises(KermaError) as ctx:
            await asyncio.wait_for(task, 1)
        self.assertEqual(ctx.exception.name, 'INVALID_ANCESTRY')

    async def test_unfindable_dependency(self):
        task = asyncio.ensure_future(self.p.wait_all([A], 2))
        await asyncio.sleep(0)
        self.p.resolve(A, UNFINDABLE)
        with self.assertRaises(KermaError) as ctx:
            await asyncio.wait_for(task, 1)
        self.assertEqual(ctx.exception.name, 'UNFINDABLE_OBJECT')

    async def test_timeout_gives_unfindable(self):
        loop = asyncio.get_running_loop()
        start = loop.time()
        with self.assertRaises(KermaError) as ctx:
            await self.p.wait_all([A], 0.2)
        self.assertEqual(ctx.exception.name, 'UNFINDABLE_OBJECT')
        self.assertIn(A, ctx.exception.msg)
        self.assertGreaterEqual(loop.time() - start, 0.19)

    async def test_waiters_cleaned_up(self):
        with self.assertRaises(KermaError):
            await self.p.wait_all([A], 0.05)
        self.assertFalse(self.p.has_waiters(A))
        self.assertEqual(self.p.resolve(A, VALID), set())

    async def test_after_register_runs_once_waiters_exist(self):
        async def deliver():
            self.assertTrue(self.p.has_waiters(A))
            self.p.resolve(A, VALID)  # an immediate reply is not missed
        await asyncio.wait_for(self.p.wait_all([A], 1, deliver), 1)

    async def test_resolve_without_waiters(self):
        self.assertEqual(self.p.resolve(A, INVALID), set())

    async def test_fail_waits_for_dependents(self):
        events = []

        async def dependent():
            try:
                await self.p.wait_all([A], 2)
            except KermaError as e:
                await asyncio.sleep(0.05)  # e.g. sending an error message
                events.append(e.name)

        task = asyncio.ensure_future(dependent())
        await asyncio.sleep(0)
        await self.p.fail(A, INVALID)
        events.append('fail returned')
        self.assertEqual(events, ['INVALID_ANCESTRY', 'fail returned'])
        await task

    async def test_multiple_waiters_for_same_object(self):
        t1 = asyncio.ensure_future(self.p.wait_all([A], 2))
        t2 = asyncio.ensure_future(self.p.wait_all([A], 2))
        await asyncio.sleep(0)
        self.p.resolve(A, VALID)
        await asyncio.wait_for(asyncio.gather(t1, t2), 1)


if __name__ == '__main__':
    unittest.main()
