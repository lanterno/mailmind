"""The new front door: the ASGI app `python -m mailmind serve` runs on uvicorn.

The one package here that imports third-party code, so the old front door (`python3 server.py`)
imports nothing from it.
"""
