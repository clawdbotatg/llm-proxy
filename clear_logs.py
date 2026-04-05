#!/usr/bin/env python3
"""Remove all proxy log files and reset counters."""

import os
import shutil

LOG_DIR = os.environ.get(
    "PROXY_LOG_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_logs"),
)

if not os.path.isdir(LOG_DIR):
    print("Nothing to clear — log directory does not exist.")
    raise SystemExit(0)

shutil.rmtree(LOG_DIR)
os.makedirs(LOG_DIR, exist_ok=True)
print(f"Cleared {LOG_DIR}")
