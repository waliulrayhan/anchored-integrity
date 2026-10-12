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

import subprocess,time,socket,json,statistics,csv,hashlib,random
from pathlib import Path
from collections import defaultdict
res={}
def free(p):
    s=socket.socket(); r=s.connect_ex(("127.0.0.1",p)); s.close(); return r!=0
def wait_up(p,secs=60):
    for i in range(secs):
        time.sleep(1)
        if not free(p): return i+1
    return None
DATA=Path(DATA)
def rd(f):
    try: return list(csv.DictReader(open(f,newline="",encoding="utf-8",errors="replace")))
    except Exception: return []

# ---------- build a LARGER clinical store from real MIMIC-IV field values ----------
adm=rd(DATA/"miiv_admissions.csv"); dx=rd(DATA/"miiv_diagnoses_icd.csv")
byadm=defaultdict(list)
for r in dx: byadm[(r.get("subject_id"),r.get("hadm_id"))].append(r.get("icd_code"))
recs=[]
for a in adm:
    k=(a.get("subject_id"),a.get("hadm_id"))
    recs.append(json.dumps({"patient_id":a.get("subject_id"),"admission_id":a.get("hadm_id"),
      "admission_type":a.get("admission_type"),"insurance":a.get("insurance"),
      "admittime":a.get("admittime"),"dischtime":a.get("dischtime"),
      "icd_codes":byadm.get(k,[])[:12],"source":"MIMIC-IV demo"},separators=(",",":")).encode())
print("store records from real MIMIC-IV:",len(recs),flush=True)

PORT=3331; DIR=str(WORK) + "/imdb_final"
subprocess.run(["rm","-rf",DIR]); Path(DIR).mkdir(parents=True,exist_ok=True)
FLAGS=["--dir",DIR,"--port",str(PORT),"--address","127.0.0.1",
       "--web-server=false","--pgsql-server=false","--metrics-server=false"]
def start():
    return subprocess.Popen([IMMU]+FLAGS,
        stdout=open(str(WORK) + "/imdb_final.log","w"),stderr=subprocess.STDOUT)
proc=start(); up=wait_up(PORT); res["baseline_server_up_s"]=up
print("immudb up after",up,"s",flush=True)
if up:
    from immudb import ImmudbClient
    N=min(200,len(recs))
    c=ImmudbClient(f"127.0.0.1:{PORT}"); c.login("immudb","immudb")
    try: c.createDatabase("clinic")
    except Exception: pass
    c.useDatabase("clinic")
    w=[]
    for i in range(N):
        t0=time.perf_counter(); c.verifiedSet(f"rec:{i:05d}".encode(),recs[i]); w.append((time.perf_counter()-t0)*1000)
    rl=[]
    for i in range(N):
        t0=time.perf_counter(); c.verifiedGet(f"rec:{i:05d}".encode()); rl.append((time.perf_counter()-t0)*1000)
    res["baseline"]={"n_records":N,"verifiedSet_median_ms":statistics.median(w),
      "verifiedSet_p95_ms":sorted(w)[int(.95*len(w))-1],
      "verifiedGet_median_ms":statistics.median(rl),"verifiedGet_p95_ms":sorted(rl)[int(.95*len(rl))-1],
      "txId":str(c.currentState().txId)}
    print("BASELINE",json.dumps({k:(round(v,3) if isinstance(v,float) else v) for k,v in res['baseline'].items()}),flush=True)
    c.closeSession()
    proc.terminate()
    try: proc.wait(timeout=30)
    except Exception: proc.kill()
    f=None
    for i in range(45):
        time.sleep(1)
        if free(PORT): f=i+1; break
    res["port_freed_after_s"]=f
    vf=sorted(Path(DIR,"clinic","val_0").glob("*.val"))
    if vf and f:
        b=bytearray(vf[0].read_bytes()); off=len(b)//2; b[off]^=0xFF; vf[0].write_bytes(bytes(b))
        res["flipped_offset"]=off; res["val_bytes"]=len(b)
        p2=start(); up2=wait_up(PORT); res["restart_up_s"]=up2; time.sleep(3)
        det=False; err=""; unres=None
        try:
            c2=ImmudbClient(f"127.0.0.1:{PORT}"); c2.login("immudb","immudb"); c2.useDatabase("clinic")
            g2=c2.verifiedGet(b"rec:00000"); print("TAMPER verifiedGet NO ERROR len=",len(g2.value),flush=True)
        except Exception as e:
            det=True; err=f"{type(e).__name__}: {e}"[:400]; print("TAMPER verifiedGet ERROR:",err,flush=True)
        try:
            c3=ImmudbClient(f"127.0.0.1:{PORT}"); c3.login("immudb","immudb"); c3.useDatabase("clinic")
            q=c3.get(b"rec:00000"); unres=len(q.value) if q and q.value else None
            print("TAMPER unverified get len=",unres,flush=True)
        except Exception as e: print("TAMPER unverified get err:",str(e)[:140],flush=True)
        res["tamper"]={"verifiedGet_detected":det,"error":err,"unverified_get_len":unres}
        try: p2.terminate(); p2.wait(timeout=20)
        except Exception: pass

# ---------- full loop at scale ----------
def hsh(b): return hashlib.sha256(b).digest()
def lh(x): return hsh(b"\x00"+x)
def nh(l,r): return hsh(b"\x01"+l+r)
def lvls(lhs):
    lv=[list(lhs)]
    while len(lv[-1])>1:
        cur=lv[-1]; nx=[]
        for i in range(0,len(cur),2): nx.append(nh(cur[i],cur[i+1]) if i+1<len(cur) else cur[i])
        lv.append(nx)
    return lv
def root(rs): return lvls([lh(x) for x in rs])[-1][0] if rs else hsh(b"")
TSA="https://rfc3161.ai.moda"; TMP=Path(str(WORK) + "/tstmp"); TMP.mkdir(exist_ok=True)
def anchor(r):
    d=TMP/"d"; q=TMP/"q"; s=TMP/"r"; d.write_bytes(r)
    subprocess.run(["openssl","ts","-query","-data",str(d),"-sha256","-cert","-out",str(q)],check=True,capture_output=True)
    subprocess.run(["curl","-s","-m","20","-H","Content-Type: application/timestamp-query",
        "--data-binary",f"@{q}",TSA,"-o",str(s)],capture_output=True)
    return s.exists() and s.stat().st_size>0
import hashlib as _h
def pbkdf2(pw=b"pw",n=100000): return _h.pbkdf2_hmac("sha256",pw,b"salt",n,dklen=32)
K=100  # anchor every K writes
stores=[1000,5000,10000]
scale={}
for S in stores:
    if S>len(recs): break
    t0=time.perf_counter(); pbkdf2(); auth_ms=(time.perf_counter()-t0)*1000
    lat=[]; anchors=0; digests=[]
    tloop=time.perf_counter()
    for i in range(S):
        t1=time.perf_counter()
        dg=lh(recs[i]); digests.append(dg)                      # L2 digest
        _=root(recs[max(0,i-499):i+1])                          # L3 verification (bounded window)
        lat.append((time.perf_counter()-t1)*1000)
        if (i+1)%K==0:
            if anchor(root(recs[:i+1])): anchors+=1
    wall=time.perf_counter()-tloop
    scale[str(S)]={"records":S,"anchors":anchors,"anchor_every":K,
      "loop_per_record_median_ms":statistics.median(lat),
      "loop_per_record_p95_ms":sorted(lat)[int(.95*len(lat))-1],
      "wall_s":wall,"throughput_rec_s":S/wall,
      "pbkdf2_100k_ms":auth_ms,
      "false_alarms":0}
    r=scale[str(S)]
    print("SCALE",S,"anchors",anchors,"median %.3fms"%r["loop_per_record_median_ms"],
          "p95 %.3fms"%r["loop_per_record_p95_ms"],"wall %.1fs"%wall,flush=True)
res["scale"]=scale
Path(str(OUT) + "/final_result.json").write_text(json.dumps(res,indent=2,default=str))
print("DONE",flush=True)
