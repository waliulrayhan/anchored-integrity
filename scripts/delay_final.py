#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# Path shim, added when packaging this project for GitHub.
#
# These scripts were written in the authoring sandbox, where inputs lived under
# /agent/... and scratch output under /scratch/... . That layout is reproduced
# here relative to the repository root, and every path can be overridden:
#
#   DATA      MIMIC demo CSVs      (default: data/mimic)
#   NSLKDD    NSL-KDD text files   (default: data/nslkdd)
#   OUT       result JSON          (default: results)
#   WORK      scratch output       (default: work)
#   IMMUDB_BIN  immudb executable  (default: "immudb" on PATH)
#
# See README.md for how to fetch the datasets and run immudb.
# ---------------------------------------------------------------------------
import os as _os
from pathlib import Path as _Path
ROOT = _Path(_os.environ.get("REPO_ROOT", _Path(__file__).resolve().parent.parent))
DATA = _Path(_os.environ.get("DATA_DIR", ROOT / "data" / "mimic"))
NSLKDD = _Path(_os.environ.get("NSLKDD_DIR", ROOT / "data" / "nslkdd"))
OUT = _Path(_os.environ.get("RESULTS_DIR", ROOT / "results"))
WORK = _Path(_os.environ.get("WORK_DIR", ROOT / "work"))
IMMU = _os.environ.get("IMMUDB_BIN", "immudb")
for _d in (DATA, NSLKDD, OUT, WORK):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
# ---------------------------------------------------------------------------

import subprocess,time,json,statistics,hashlib,random
from pathlib import Path
TMP=Path(str(WORK) + "/t4"); TMP.mkdir(exist_ok=True)
def h(b): return hashlib.sha256(b).digest()
def lh(x): return h(b"\x00"+x)
def nh(l,r): return h(b"\x01"+l+r)
def lvls(lhs):
    lv=[list(lhs)]
    while len(lv[-1])>1:
        cur=lv[-1]; nx=[]
        for i in range(0,len(cur),2): nx.append(nh(cur[i],cur[i+1]) if i+1<len(cur) else cur[i])
        lv.append(nx)
    return lv
def root(rs): return lvls([lh(x) for x in rs])[-1][0] if rs else h(b"")
TSA="https://rfc3161.ai.moda"
def anchor(r):
    d=TMP/"d"; q=TMP/"q"; s=TMP/"r"; d.write_bytes(r)
    subprocess.run(["openssl","ts","-query","-data",str(d),"-sha256","-cert","-out",str(q)],check=True,capture_output=True)
    t0=time.perf_counter()
    subprocess.run(["curl","-s","-m","20","-H","Content-Type: application/timestamp-query",
        "--data-binary",f"@{q}",TSA,"-o",str(s)],capture_output=True)
    rtt=(time.perf_counter()-t0)*1000
    return (s.exists() and s.stat().st_size>0), rtt
# selftest
R0=[b"a%d"%i for i in range(300)]; p0=root(R0)
assert root(R0)==p0
T0=list(R0); T0[5]=T0[5]+b"|x"; assert root(T0)!=p0
print("SELFTEST ok",flush=True)
rng=random.Random(777)
def mk(n,b=0): return [f"rec-{b+i}".encode() for i in range(n)]
out={}; allrows=[]
for T,trials in [(0.2,40),(1.0,40),(5.0,40)]:
    R=mk(150); k=len(R); pub=root(R[:k]); anchor(pub)
    tref=time.perf_counter(); rows=[]
    for t in range(trials):
        idx=rng.randrange(0,k); off=rng.random()
        ta=tref+off*T
        while time.perf_counter()<ta: time.sleep(min(0.02,ta-time.perf_counter()))
        orig=R[idx]; R[idx]=orig+b"|tampered"
        due=tref+T
        while time.perf_counter()<due-0.001: time.sleep(min(0.02,due-time.perf_counter()))
        det=(root(R[:k])!=pub)
        d=(time.perf_counter()-ta)*1000
        rows.append({"detected":det,"delay_ms":d,"offset_frac":off})
        assert det
        R[idx]=orig; R.append(mk(1,b=5000+t)[0]); k=len(R)
        pub=root(R[:k]); anchor(pub); tref=time.perf_counter()
    ds=[r["delay_ms"] for r in rows]
    out[str(T)]={"T_s":T,"trials":len(rows),"detected":sum(r["detected"] for r in rows),
      "mean_ms":statistics.mean(ds),"median_ms":statistics.median(ds),"min_ms":min(ds),"max_ms":max(ds),
      "p95_ms":sorted(ds)[int(.95*len(ds))-1],"stdev_ms":statistics.pstdev(ds),
      "ideal_mean_ms":T*1000/2,"ideal_max_ms":T*1000}
    r=out[str(T)]
    print(f"T={T}s n={r['trials']} det={r['detected']}/{r['trials']} mean={r['mean_ms']:.0f} median={r['median_ms']:.0f} "
          f"min={r['min_ms']:.0f} max={r['max_ms']:.0f} (ideal {r['ideal_mean_ms']:.0f}/{r['ideal_max_ms']:.0f})",flush=True)
    allrows+= [{"T_s":T,**r2} for r2 in rows]
json.dump({"intervals":out,"rows":allrows},open(str(OUT) + "/delay_final.json","w"),indent=2)
print("DONE",flush=True)
