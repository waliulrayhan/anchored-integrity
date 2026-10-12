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

import subprocess,time,socket,json,csv,statistics
from pathlib import Path
from collections import defaultdict
DATA=Path(DATA)
def rd(f):
    try: return list(csv.DictReader(open(f,newline="",encoding="utf-8",errors="replace")))
    except Exception: return []
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

def free(p):
    s=socket.socket(); r=s.connect_ex(("127.0.0.1",p)); s.close(); return r!=0
def wait_up(p,secs=60):
    for i in range(secs):
        time.sleep(1)
        if not free(p): return i+1
    return None
PORT=3341; DIR=str(WORK) + "/imdb_t2"
subprocess.run(["rm","-rf",DIR]); Path(DIR).mkdir(parents=True,exist_ok=True)
FLAGS=["--dir",DIR,"--port",str(PORT),"--address","127.0.0.1",
       "--web-server=false","--pgsql-server=false","--metrics-server=false"]
def start():
    return subprocess.Popen([IMMU]+FLAGS,
        stdout=open(str(WORK) + "/it2.log","w"),stderr=subprocess.STDOUT)
r={}
p=start(); r["up1"]=wait_up(PORT); print("up1",r["up1"],flush=True)
from immudb import ImmudbClient
c=ImmudbClient(f"127.0.0.1:{PORT}"); c.login("immudb","immudb")
try: c.createDatabase("clinic")
except Exception: pass
c.useDatabase("clinic")
N=200
tgt=recs[0]
w=[]
for i in range(N):
    t0=time.perf_counter(); c.verifiedSet(f"rec:{i:05d}".encode(),recs[i%len(recs)]); w.append((time.perf_counter()-t0)*1000)
rl=[]
for i in range(N):
    t0=time.perf_counter(); c.verifiedGet(f"rec:{i:05d}".encode()); rl.append((time.perf_counter()-t0)*1000)
r["baseline"]={"n_records":N,"verifiedSet_median_ms":statistics.median(w),
  "verifiedSet_p95_ms":sorted(w)[int(.95*len(w))-1],
  "verifiedGet_median_ms":statistics.median(rl),"verifiedGet_p95_ms":sorted(rl)[int(.95*len(rl))-1],
  "txId":str(c.currentState().txId)}
print("baseline",json.dumps({k:(round(v,3) if isinstance(v,float) else v) for k,v in r["baseline"].items()}),flush=True)
del c
p.terminate()
try: p.wait(timeout=45)
except Exception: p.kill(); p.wait(timeout=15)
for i in range(60):
    time.sleep(1)
    if free(PORT): r["port_freed_s"]=i+1; break
print("port freed",r.get("port_freed_s"),flush=True)

# locate rec:00000's value bytes inside the value log and flip a byte INSIDE them
vfs=sorted(Path(DIR,"clinic","val_0").glob("*.val"))
print("val files",[str(x) for x in vfs],flush=True)
found=False
for vf in vfs:
    b=bytearray(vf.read_bytes())
    idx=b.find(tgt)
    if idx<0: idx=b.find(tgt[:60])
    print("search",vf.name,"len",len(b),"-> offset",idx,flush=True)
    if idx>=0:
        off=idx+len(tgt)//2
        b[off]^=0xFF
        vf.write_bytes(bytes(b))
        r.update({"val_file":vf.name,"val_bytes":len(b),"record_offset":idx,"flipped_offset":off,
                  "record_len":len(tgt)})
        found=True
        break
r["found_record_bytes"]=found
p2=start(); r["up2"]=wait_up(PORT); time.sleep(4)
print("up2",r["up2"],flush=True)
det=False; err=""; same=None; got_len=None
try:
    c2=ImmudbClient(f"127.0.0.1:{PORT}"); c2.login("immudb","immudb"); c2.useDatabase("clinic")
    g=c2.verifiedGet(b"rec:00000")
    got=bytes(g.value); got_len=len(got); same=(got==tgt)
    det = not same
    print("verifiedGet returned len",got_len,"identical_to_original",same,flush=True)
except Exception as e:
    det=True; err=f"{type(e).__name__}: {e}"[:400]; print("verifiedGet ERROR:",err,flush=True)
# unverified get for contrast
try:
    c3=ImmudbClient(f"127.0.0.1:{PORT}"); c3.login("immudb","immudb"); c3.useDatabase("clinic")
    q=c3.get(b"rec:00000"); uv=(bytes(q.value)==tgt) if q and q.value else None
    print("unverified get identical_to_original",uv,flush=True)
except Exception as e:
    uv=None; print("unverified get err",str(e)[:140],flush=True)
r["tamper"]={"verified_get_detected":det,"verified_get_error":err,
   "verified_get_identical_to_original":same,"verified_get_len":got_len,"unverified_get_identical":uv}
try: p2.terminate(); p2.wait(timeout=20)
except Exception: pass
Path(str(OUT) + "/baseline_result.json").write_text(json.dumps(r,indent=2,default=str))
print("DONE",json.dumps({k:v for k,v in r.items() if k in('found_record_bytes','record_offset','flipped_offset')}),flush=True)
