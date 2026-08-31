"""Vercel entry point.

Vercel looks for an ASGI callable named `app` in this file and runs it as one
serverless function. Everything else is the same application that runs on a
server — same reader, same rules, same screens.

What differs is set by VERCEL=1 in the environment, which switches
`Settings.serverless` on. See app/config.py for what that changes and why.

If importing the application fails, this serves a page saying why. A
serverless host answers an import error with FUNCTION_INVOCATION_FAILED and
nothing else — no traceback, no clue which line — and the real message is
several clicks away in a log viewer. Since the most likely reason to be
looking at this file at all is that something failed to start, it is worth
the twenty lines to have the failure explain itself in the browser.
"""
from __future__ import annotations

import os
import sys
import traceback

try:
    from app.main import app
except Exception:  # noqa: BLE001 - the whole point is to report anything
    _TRACE = traceback.format_exc()

    def _report() -> str:
        # Names only. A value here could be a database password.
        seen = sorted(
            k for k in os.environ
            if k.startswith(("POSTGRES", "DATABASE", "VERCEL", "SERVERLESS",
                             "EXTRACTION", "ENABLE_", "HOME_STATE"))
        )
        return (
            "Invoice Ledger failed to start.\n\n"
            f"Python: {sys.version}\n"
            f"Environment variables present: {', '.join(seen) or 'none'}\n"
            f"Database configured: "
            f"{'yes' if any(k.startswith(('POSTGRES', 'DATABASE')) for k in os.environ) else 'NO'}\n"
            f"\n{'-' * 60}\n{_TRACE}"
        )

    async def app(scope, receive, send):  # type: ignore[misc]
        """A minimal ASGI app that reports the import failure."""
        if scope["type"] != "http":
            return
        body = _report().encode()
        await send({
            "type": "http.response.start",
            "status": 500,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        })
        await send({"type": "http.response.body", "body": body})


__all__ = ["app"]
