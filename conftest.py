"""Pytest bootstrap, loaded before any test module is imported.

`core.config` resolves DATABASE_URL at import time and raises when it is absent.
That is the right behaviour for the app (a misconfigured server should fail
loudly at boot rather than deep inside a query) but the wrong behaviour for the
unit tests: not one of them opens a connection, yet all of them import a module
that transitively imports config. Without this file, a fresh clone fails
collection with 7 errors before running a single test.

This lives at the repo root rather than in tests/ on purpose. Pytest also
collects `smoke_test.py` from the root (it matches the default `*_test.py`
pattern), and only a rootdir conftest is loaded early enough to cover it.

A real .env still wins: it is loaded first, so the placeholder below fills in
only when nothing else supplied a value. Nothing in the unit suite connects to
it; it exists so that the import succeeds.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv(override=False)

os.environ.setdefault("DATABASE_URL", "postgresql://rag:ragpass@localhost:5433/ragdb")
