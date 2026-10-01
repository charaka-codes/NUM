import subprocess
print("running nettop for ~25s (qBittorrent should be downloading)...\n")
out=subprocess.check_output(["nettop","-P","-x","-s","5","-l","5",
    "-J","bytes_in,bytes_out"],text=True,stderr=subprocess.DEVNULL)
snaps,cur=[],[]
for line in out.splitlines():
    if "bytes_in" in line and "bytes_out" in line:
        if cur: snaps.append(cur)
        cur=[]; continue
    if line.strip(): cur.append(line)
if cur: snaps.append(cur)
print("snapshots captured:",len(snaps))
series={}
for i,s in enumerate(snaps):
    for line in s:
        p=line.split()
        if len(p)<3: continue
        try: bout=int(p[-1]); bin_=int(p[-2])
        except ValueError: continue
        name=" ".join(p[:-2])
        if not (name.startswith("qbittorrent") or name.startswith("Google Chrome")): continue
        series.setdefault(name,[None]*len(snaps))[i]=bin_
print()
for k in sorted(series):
    print(f"{k:<26}",series[k])
print("\nVERDICT")
for k in sorted(series):
    v=[x for x in series[k] if x is not None]
    if len(v)<3: continue
    climbing=all(b>=a for a,b in zip(v,v[1:]))
    grew=v[-1]>v[0]*1.5
    if climbing and grew: print(f"  {k:<26} CUMULATIVE  -> diff consecutive reports")
    elif climbing:        print(f"  {k:<26} climbing but slowly - paste numbers")
    else:                 print(f"  {k:<26} PER-INTERVAL -> SUM the reports")
