#!/usr/bin/env python3
"""Generate the HTML dashboard and open it. Usage: python3 -m netmonitor.make_dashboard [YYYY-MM]"""
import subprocess, sys
from datetime import datetime
from . import core, dashboard

month = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m")
con = core.connect()
out = core.DB_PATH.parent / "dashboard.html"
dashboard.write_dashboard(con, str(out), month)
print(f"Dashboard written to {out}")
try:
    subprocess.run(["open", str(out)])
except Exception:
    pass
