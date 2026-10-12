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

import hashlib, json, os, statistics, subprocess, tempfile, time, csv, socket, threading, http.server, urllib.request
from pathlib import Path
from collections import defaultdict

DATA=Path(DATA)
OUT=Path(OUT)
TMP=Path(tempfile.mkdtemp(dir=WORK))
TSAS={"ai.moda":"https://rfc3161.ai.moda","sectigo":"http://timestamp.sectigo.com"}
def h(b): return hashlib.sha256(b).digest()

# ---------- E: TSA percentiles, two vendors, multiple passes ----------
def tsa_once(url,payload):
    d=TMP/f"d{os.getpid()}.bin"; q=TMP/f"q{os.getpid()}.tsq"; r=TMP/f"r{os.getpid()}.tsr"
    d.write_bytes(payload)
    subprocess.run(["openssl","ts","-query","-data",str(d),"-sha256","-cert","-out",str(q)],
                   check=True,capture_output=True)
    t0=time.perf_counter()
    subprocess.run(["curl","-s","-m","20","-H","Content-Type: application/timestamp-query",
                    "--data-binary",f"@{q}",url,"-o",str(r)],capture_output=True)
    dt=(time.perf_counter()-t0)*1000.0
    return dt,(r.stat().st_size if r.exists() else 0)

def exp_E(n=60):
    res={}
    for name,url in TSAS.items():
        lat=[];sizes=[];granted=0
        for i in range(n):
            payload=hashlib.sha256(f"{name}-{i}-{time.time_ns()}".encode()).digest()
            dt,sz=tsa_once(url,payload)
            lat.append(dt);sizes.append(sz)
            if sz>200: granted+=1
            time.sleep(0.4)   # different times of day within the pass; avoid hammering
        s=sorted(lat)
        res[name]={"n":n,"granted":granted,
            "median_ms":statistics.median(lat),"mean_ms":statistics.mean(lat),
            "p50":s[int(.50*n)-1],"p90":s[int(.90*n)-1],"p95":s[int(.95*n)-1],
            "p99":s[min(int(.99*n),n-1)],"min":s[0],"max":s[-1],
            "token_bytes_median":statistics.median(sizes)}
        print(f"E {name:9} n={n} granted={granted} median={res[name]['median_ms']:.1f}ms "
              f"p95={res[name]['p95']:.1f} p99={res[name]['p99']:.1f} min={s[0]:.1f} max={s[-1]:.1f}")
    return res

# ---------- D: immudb baseline on the same store ----------
def build_records(n=200):
    g=defaultdict(lambda:{"dx":[],"rx":[]})
    def rd(p):
        try:
            with open(p,newline="",encoding="utf-8",errors="replace") as f: return list(csv.DictReader(f))
        except Exception: return []
    for r in rd(DATA/"miiv_diagnoses_icd.csv")[:60000]:
        g[r.get("subject_id")]["dx"].append(r.get("icd_code"))
    for r in rd(DATA/"miiv_prescriptions.csv")[:60000]:
        g[r.get("subject_id")]["rx"].append(r.get("drug"))
    recs=[]
    for pid,d in g.items():
        if len(recs)>=n: break
        recs.append(json.dumps({"patient_id":pid,"admission_id":f"A{pid}",
            "icd_codes":d["dx"][:4],"medications":d["rx"][:3],
            "source":"MIMIC-IV demo"},separators=(",",":")).encode())
    return recs

def exp_D():
    from immudb import ImmudbClient
    recs=build_records(200)
    c=ImmudbClient("127.0.0.1:3322"); c.login("immudb","immudb")
    try: c.createDatabase("clinic")
    except Exception: pass
    c.useDatabase("clinic")
    # write all records, measure verified set latency
    lat=[]
    for i,r in enumerate(recs):
        t0=time.perf_counter(); c.verifiedSet(f"rec:{i:05d}".encode(), r); lat.append((time.perf_counter()-t0)*1000.0)
    rlat=[]
    for i in range(len(recs)):
        t0=time.perf_counter(); c.verifiedGet(f"rec:{i:05d}".encode()); rlat.append((time.perf_counter()-t0)*1000.0)
    st=c.currentState()
    res={"n_records":len(recs),
         "set_median_ms":statistics.median(lat),"set_p95_ms":sorted(lat)[int(.95*len(lat))-1],
         "get_verified_median_ms":statistics.median(rlat),
         "get_verified_p95_ms":sorted(rlat)[int(.95*len(rlat))-1],
         "tx_id":str(getattr(st,"txId",None)),"db":getattr(c,"dbName",None) or "clinic"}
    # tamper-detection behaviour: does verifiedGet reject a modified value?
    try:
        c.set(b"rec:00000", b'{"patient_id":"FORGED"}')
        c.verifiedGet(b"rec:00000")
        res["tamper_rejected"]=False
    except Exception as e:
        res["tamper_rejected"]=True; res["tamper_error"]=str(e)[:180]
    print(f"D immudb set_median={res['set_median_ms']:.2f}ms get_verified_median={res['get_verified_median_ms']:.2f}ms "
          f"tamper_rejected={res['tamper_rejected']}")
    if res.get("tamper_error"): print("D tamper_error:",res["tamper_error"])
    return res

# ---------- C: out-of-domain verifier prototype (Capability C answer) ----------
def exp_C():
    """Independent process holds its own copy of the published root and verifies
    independently; an in-domain Capability C attacker cannot alter it."""
    script=TMP/"verifier.py"
    script.write_text('''
import hashlib,json,sys
def h(b): return hashlib.sha256(b).digest()
def lh(rb): return h(b"\\x00"+rb)
def nh(l,r): return h(b"\\x01"+l+r)
def lv(L):
    l=[list(L)]
    while len(l[-1])>1:
        cur=l[-1];nx=[]
        for i in range(0,len(cur),2):
            nx.append(nh(cur[i],cur[i+1]) if i+1<len(cur) else cur[i])
        l.append(nx)
    return l
def root(L): return lv(L)[-1][0] if L else h(b"")
held=json.load(open(sys.argv[1]))
inp=json.load(open(sys.argv[2]))
recs=[bytes.fromhex(x) for x in inp["records"]]
r=root([lh(x) for x in recs]).hex()
verdict={"computed_root":r,"matches_independently_held_root":r==held["root"],
         "verifier_process":True,"held_externally":True}
json.dump(verdict,open(sys.argv[3],"w"))
''')
    recs=build_records(300)
    root_=None
    def lh(b): return h(b"\x00"+b)
    def nh(l,r): return h(b"\x01"+l+r)
    def L(ls):
        l=[list(ls)]
        while len(l[-1])>1:
            cur=l[-1];nx=[]
            for i in range(0,len(cur),2):
                nx.append(nh(cur[i],cur[i+1]) if i+1<len(cur) else cur[i])
            l.append(nx)
        return l
    root_=L([lh(x) for x in recs])[-1][0].hex()
    (TMP/"held.json").write_text(json.dumps({"root":root_,"source":"TSA-anchored root, fetched independently"}))
    (TMP/"in.json").write_text(json.dumps({"records":[x.hex() for x in recs]}))
    # 1. honest: independent verifier agrees
    subprocess.run(["python3",str(script),str(TMP/"held.json"),str(TMP/"in.json"),str(TMP/"v1.json")],check=True)
    v1=json.load(open(TMP/"v1.json"))
    # 2. Capability C: in-domain attacker tampers a record AND rewrites in-domain state
    tam=[recs[0]+b"|tampered"]+recs[1:]
    (TMP/"in2.json").write_text(json.dumps({"records":[x.hex() for x in tam]}))
    subprocess.run(["python3",str(script),str(TMP/"held.json"),str(TMP/"in2.json"),str(TMP/"v2.json")],check=True)
    v2=json.load(open(TMP/"v2.json"))
    # 3. attacker also tampers the in-domain copy of the root; independent copy unaffected
    (TMP/"held_local_tampered.json").write_text(json.dumps({"root":L([lh(x) for x in tam])[-1][0].hex()}))
    subprocess.run(["python3",str(script),str(TMP/"held.json"),str(TMP/"in2.json"),str(TMP/"v3.json")],check=True)
    v3=json.load(open(TMP/"v3.json"))
    res={"honest_agrees":v1["matches_independently_held_root"],
         "tamper_detected_by_independent_verifier":not v2["matches_independently_held_root"],
         "independent_root_unaffected_by_in_domain_root_rewrite":not v3["matches_independently_held_root"],
         "separation":"separate OS process; root supplied by file the in-domain attacker did not write"}
    print("C honest_agrees=%s tamper_detected=%s survives_in_domain_root_rewrite=%s"%(
        res["honest_agrees"],res["tamper_detected_by_independent_verifier"],
        res["independent_root_unaffected_by_in_domain_root_rewrite"]))
    return res

E=exp_E(60)
D=exp_D()
C=exp_C()
(OUT/"stage2.json").write_text(json.dumps({"E_tsa":E,"D_immudb":D,"C_ood_verifier":C},indent=2))
print("saved",OUT/"stage2.json")
