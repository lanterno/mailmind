"""The app's HTTP: the handler and route table both front doors serve, and the ASGI app `python -m mailmind serve`
runs on uvicorn.

Only `app`, `bridge`, `middleware` and `serving` import third-party code, so the old front door (`python3 server.py`) imports
none of those four.
"""
