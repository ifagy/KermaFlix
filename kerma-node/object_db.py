"""Persistent, content-addressed object database backed by SQLite."""

import json
import logging
import os
import sqlite3
from typing import Any, Dict, Optional

from objects import GENESIS_BLOCK, GENESIS_ID, canonical_json, object_id

logger = logging.getLogger('ObjectDB')


class ObjectDB:
    """Stores application objects by objectid; survives restarts.

    The genesis block is always present.
    """

    def __init__(self, path: Optional[str] = None):
        """Open (or create) the database at path.

        path defaults to the OBJECTS_DB environment variable or 'objects.db'.
        """
        self.path = path or os.environ.get('OBJECTS_DB', 'objects.db')
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            'CREATE TABLE IF NOT EXISTS objects ('
            '  objectid TEXT PRIMARY KEY,'
            '  type TEXT NOT NULL,'
            '  data TEXT NOT NULL'
            ')'
        )
        self.conn.commit()
        assert object_id(GENESIS_BLOCK) == GENESIS_ID, 'genesis block id mismatch'
        self.put(GENESIS_BLOCK)
        logger.info(f'Object database {self.path} contains {self.count()} objects')

    def has(self, oid: str) -> bool:
        """True if an object with this id is stored."""
        row = self.conn.execute(
            'SELECT 1 FROM objects WHERE objectid = ?', (oid,)
        ).fetchone()
        return row is not None

    def get(self, oid: str) -> Optional[Dict[str, Any]]:
        """Return the stored object with this id, or None."""
        row = self.conn.execute(
            'SELECT data FROM objects WHERE objectid = ?', (oid,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, obj: Dict[str, Any]) -> str:
        """Store obj under its objectid (no-op if already stored) and return the id."""
        oid = object_id(obj)
        self.conn.execute(
            'INSERT OR IGNORE INTO objects (objectid, type, data) VALUES (?, ?, ?)',
            (oid, str(obj.get('type')), canonical_json(obj).decode('utf-8')),
        )
        self.conn.commit()
        return oid

    def count(self) -> int:
        """Return the number of stored objects."""
        return self.conn.execute('SELECT COUNT(*) FROM objects').fetchone()[0]

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
