"""The old front door: `python3 server.py [port]`, then open http://127.0.0.1:<port>.

The app itself is the mailmind package, and this runs it with nothing installed (mailmind.bootstrap.old_door).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from mailmind.bootstrap import old_door

if __name__ == "__main__":
    old_door.main(sys.argv)
