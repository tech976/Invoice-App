"""Vercel entry point.

Vercel looks for an ASGI callable named `app` in this file and runs it as one
serverless function. Everything else is the same application that runs on a
server — same reader, same rules, same screens.

What differs is set by VERCEL=1 in the environment, which switches
`Settings.serverless` on. See app/config.py for what that changes and why.
"""
from app.main import app

__all__ = ["app"]
