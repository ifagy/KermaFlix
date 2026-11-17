"""Bookkeeping for objects whose validation is on hold until other objects arrive."""

import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Set

from objects import KermaError

# Outcomes of an object's validation.
VALID = 'valid'
INVALID = 'invalid'        # the object turned out to be invalid -> INVALID_ANCESTRY
UNFINDABLE = 'unfindable'  # the object could not be verified -> UNFINDABLE_OBJECT


class PendingObjects:
    """Lets validations wait for other objects and be notified of their outcome."""

    def __init__(self):
        self._waiters: Dict[str, List[asyncio.Future]] = defaultdict(list)
        self._dependents: Dict[str, Set[asyncio.Task]] = defaultdict(set)
        self._in_progress: Set[str] = set()

    # ---------- Objects being validated ----------

    def start(self, oid: str) -> None:
        """Mark oid as currently being validated (its outcome will be reported)."""
        self._in_progress.add(oid)

    def in_progress(self, oid: str) -> bool:
        """True if oid is currently being validated."""
        return oid in self._in_progress

    # ---------- Outcomes ----------

    def resolve(self, oid: str, status: str) -> Set[asyncio.Task]:
        """Report the outcome of oid to everyone waiting for it.

        Returns the tasks that were waiting (excluding the current task).
        """
        self._in_progress.discard(oid)
        for fut in self._waiters.pop(oid, []):
            if not fut.done():
                fut.set_result(status)
        tasks = self._dependents.pop(oid, set())
        tasks.discard(asyncio.current_task())
        return tasks

    async def fail(self, oid: str, status: str, timeout: float = 2.0) -> None:
        """Report a failed validation and give the dependent validations time to react.

        This lets dependents send their errors (e.g. INVALID_ANCESTRY) before the
        caller closes its connection.
        """
        tasks = self.resolve(oid, status)
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)

    # ---------- Waiting ----------

    def has_waiters(self, oid: str) -> bool:
        return bool(self._waiters.get(oid))

    async def wait_all(
            self,
            oids: Iterable[str],
            timeout: float,
            after_register: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        """Wait until all oids were validated successfully.

        after_register (optional) is awaited once the waiters are registered,
        e.g. to request the objects without missing a fast reply.
        Raises INVALID_ANCESTRY if one of them is invalid and UNFINDABLE_OBJECT
        if one could not be verified or did not arrive within timeout seconds.
        """
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        futures = {}
        for oid in dict.fromkeys(oids):
            fut = loop.create_future()
            self._waiters[oid].append(fut)
            if task is not None:
                self._dependents[oid].add(task)
            futures[fut] = oid

        try:
            deadline = loop.time() + timeout
            if after_register is not None:
                await after_register()
            pending = set(futures)
            while pending:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    missing = sorted(futures[f] for f in pending)
                    raise KermaError(
                        'UNFINDABLE_OBJECT',
                        f'Did not receive {", ".join(missing)} within {timeout:g} seconds',
                    )
                done, pending = await asyncio.wait(
                    pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
                for fut in done:
                    oid, status = futures[fut], fut.result()
                    if status == INVALID:
                        raise KermaError('INVALID_ANCESTRY', f'Object {oid} is invalid')
                    if status == UNFINDABLE:
                        raise KermaError('UNFINDABLE_OBJECT', f'Object {oid} could not be verified')
        finally:
            for fut, oid in futures.items():
                waiters = self._waiters.get(oid)
                if waiters is not None and fut in waiters:
                    waiters.remove(fut)
                    if not waiters:
                        del self._waiters[oid]
                deps = self._dependents.get(oid)
                if deps is not None:
                    deps.discard(task)
                    if not deps:
                        del self._dependents[oid]
