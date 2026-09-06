# config/__init__.py
"""ULTRON configuration package.

The single source of truth is `config/loader.py` (P0-D) — import that:

    from config import loader
    cfg = loader.load_config()

The legacy cached reader that used to live in this file was removed in P0-D2.
"""
