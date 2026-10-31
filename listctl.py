#!/usr/bin/env python3
"""
Root entrypoint for listctl CLI.
Delegates to listing/listctl.py.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from listing.listctl import main

if __name__ == "__main__":
    main()
