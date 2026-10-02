"""Test plumbing.

The generated test classes call ``self.assertIsNotNone`` without
subclassing ``unittest.TestCase``. Inject unittest assertion helpers
onto collected classes that lack them so the tests can run. Test files
themselves are left untouched.
"""

import unittest


def pytest_collection_modifyitems(items):
    for item in items:
        cls = getattr(item, "cls", None)
        if cls is None or issubclass(cls, unittest.TestCase):
            continue
        for name in dir(unittest.TestCase):
            if name.startswith("assert") and not hasattr(cls, name):
                setattr(cls, name, getattr(unittest.TestCase, name))
