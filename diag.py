import os,sys,time,sqlite3,traceback
os.environ.setdefault("NUM_PROFILE","v2")
sys.path.insert(0,os.getcwd())
from netmonitor import store, apps as A, core
G=1024**3; M=store.current_month()
print("dir:",store.DATA_DIR," months:",store.available_months(),
      " sealed:",{m:store.is_sealed(m) for m in store.available_months()})
print(f"{'time':<10}{'L1 nettop':>12}{'L2 db':>12}{'rows':>7}{'L3 payload':>13}")
f={}
for i in range(6):
    t=time.strftime("%H:%M:%S")
    try:
        s=A._read_nettop(); d=sum(v[0] for k,v in s.items() if k.startswith("Google Chrome"))
        l1="%.3f"%(d/G)
    except Exception: l1="ERR"; traceback.print_exc()
    try:
        p=store.month_path(M); c=sqlite3.connect(p); c.execute("PRAGMA journal_mode=WAL")
        r=c.execute("SELECT COALESCE(SUM(down),0) FROM app_usage WHERE app LIKE 'Google Chrome%'").fetchone()
        n=c.execute("SELECT COUNT(*) FROM app_usage").fetchone()[0]; c.close()
        l2="%.3f"%(r[0]/G); rows=str(n)
    except Exception: l2="ERR"; rows="-"; traceback.print_exc()
    try:
        pl=A.app_dashboard_payload(core.connect(),"month",M,top=25)
        v=[a["down"] for a in pl["apps"] if a["app"].startswith("Google Chrome")]
        l3="%.3f"%((v[0] if v else 0)/G)
    except Exception:
        l3="ERR"; print("\n*** LAYER 3 EXCEPTION (the app hides this) ***"); traceback.print_exc()
    print(f"{t:<10}{l1:>12}{l2:>12}{rows:>7}{l3:>13}")
    for k,v in (("1",l1),("2",l2),("3",l3)): f.setdefault(k,v)
    if i<5: time.sleep(15)
print("\nVERDICT")
for k,lab,now in (("1","L1 nettop  ",l1),("2","L2 month db",l2),("3","L3 payload ",l3)):
    print(f"  {lab}: {'BROKEN' if now=='ERR' else ('FROZEN <-- stuck' if now==f[k] else 'moving %s -> %s'%(f[k],now))}")
