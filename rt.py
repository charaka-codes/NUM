import os,sys,time
os.environ.setdefault("NUM_PROFILE","v2")
sys.path.insert(0,os.getcwd())
from netmonitor.apps import NettopReader
r=NettopReader(interval=5); r.start()
print("watching the reader for 30s...\n")
for i in range(6):
    time.sleep(5)
    s=r.snapshot()
    qb=[v[0] for k,v in s.items() if k.startswith("qbittorrent")]
    print(f"  t={i*5+5:>2}s  reports={r.reports_seen():<3} processes={len(s):<4} qbittorrent={qb[0] if qb else '-'}")
r.stop()
print("\nreports=0 and processes=0  -> nettop output is buffered (the bug)")
print("reports climbing, qbittorrent growing -> reader is fine, problem is elsewhere")
