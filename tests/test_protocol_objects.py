"""Tests for validation of the object exchange messages (protocol.py, connection parsing)."""

import unittest

import helpers  # noqa: F401  (sets up the import path)
from connection import ConnectionHandler
from protocol import validate_message

OID = 'ab' * 32


class ObjectMessageValidationTest(unittest.TestCase):
    def assertValid(self, msg):
        ok, name, detail = validate_message(msg)
        self.assertTrue(ok, detail)

    def assertInvalid(self, msg):
        ok, name, _ = validate_message(msg)
        self.assertFalse(ok, msg)
        self.assertEqual(name, 'INVALID_FORMAT')

    def test_valid_getobject_and_ihaveobject(self):
        for t in ('getobject', 'ihaveobject'):
            self.assertValid({'type': t, 'objectid': OID})

    def test_objectid_missing(self):
        for t in ('getobject', 'ihaveobject'):
            self.assertInvalid({'type': t})

    def test_objectid_invalid(self):
        for t in ('getobject', 'ihaveobject'):
            for bad in ('ab' * 31, 'AB' * 32, 'xy' * 32, 5, None, ['ab' * 32]):
                self.assertInvalid({'type': t, 'objectid': bad})

    def test_objectid_additional_key(self):
        for t in ('getobject', 'ihaveobject'):
            self.assertInvalid({'type': t, 'objectid': OID, 'extra': 1})

    def test_valid_object_message(self):
        self.assertValid({'type': 'object', 'object': {'type': 'transaction'}})

    def test_object_missing(self):
        self.assertInvalid({'type': 'object'})

    def test_object_not_a_dict(self):
        for bad in ([], 'x', 1, None):
            self.assertInvalid({'type': 'object', 'object': bad})

    def test_object_additional_key(self):
        self.assertInvalid({'type': 'object', 'object': {}, 'objectid': OID})

    def test_still_ignored_types_are_valid(self):
        for msg in ({'type': 'getchaintip'}, {'type': 'getmempool'},
                    {'type': 'chaintip', 'blockid': OID}, {'type': 'mempool', 'txids': []}):
            self.assertValid(msg)


class JsonParsingTest(unittest.TestCase):
    def test_parses_valid_json(self):
        msg, err = ConnectionHandler._parse_line(b'{"type":"getpeers"}\n')
        self.assertIsNone(err)
        self.assertEqual(msg, {'type': 'getpeers'})

    def test_rejects_nan_and_infinity(self):
        for raw in (b'{"v":NaN}', b'{"v":Infinity}', b'{"v":-Infinity}'):
            msg, err = ConnectionHandler._parse_line(raw)
            self.assertIsNotNone(err, raw)

    def test_rejects_invalid_utf8(self):
        _, err = ConnectionHandler._parse_line(b'\xff\xfe')
        self.assertIsNotNone(err)


if __name__ == '__main__':
    unittest.main()
