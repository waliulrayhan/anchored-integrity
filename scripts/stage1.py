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

import hashlib, json, os, random, statistics, subprocess, tempfile, time, csv
from pathlib import Path
from collections import defaultdict

DATA=Path(DATA)
OUT=Path(OUT); OUT.mkdir(parents=True, exist_ok=True)
TMP=Path(tempfile.mkdtemp(dir=WORK))
SEED=20261011
rng=random.Random(SEED)
TSA="https://rfc3161.ai.moda"

def h(b): return hashlib.sha256(b).digest()
def lhash(rb): return h(b"\x00"+rb)
def nhash(l,r): return h(b"\x01"+l+r)

def build_levels(L):
    lv=[list(L)]
    while len(lv[-1])>1:
        cur=lv[-1]; nxt=[]
        for i in range(0,len(cur),2):
            nxt.append(nhash(cur[i],cur[i+1]) if i+1<len(cur) else cur[i])
        lv.append(nxt)
    return lv
def root_of(L): return build_levels(L)[-1][0] if L else h(b"")

# ---------------- real TSA ----------------
def tsa_publish(root):
    d=TMP/"d.bin"; q=TMP/"q.tsq"; r=TMP/"r.tsr"
    d.write_bytes(root)
    subprocess.run(["openssl","ts","-query","-data",str(d),"-sha256","-cert","-out",str(q)],
                   check=True, capture_output=True)
    t0=time.perf_counter()
    subprocess.run(["curl","-s","-m","20","-H","Content-Type: application/timestamp-query",
                    "--data-binary",f"@{q}",TSA,"-o",str(r)],capture_output=True)
    t1=time.perf_counter()
    return (t1-t0)*1000.0, (r.stat().st_size if r.exists() else 0)

# ---------------- records from real MIMIC ----------------
def rd(p):
    try:
        with open(p, newline="", encoding="utf-8", errors="replace") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []

def build_records(n=200):
    g=defaultdict(lambda:{"dx":[],"rx":[]})
    for r in rd(DATA/"miiv_diagnoses_icd.csv")[:60000]:
        g[r.get("subject_id")]["dx"].append(r.get("icd_code"))
    for r in rd(DATA/"miiv_prescriptions.csv")[:60000]:
        g[r.get("subject_id")]["rx"].append(r.get("drug"))
    recs=[]
    for pid,d in g.items():
        if len(recs)>=n: break
        rec=json.dumps({"patient_id":pid,"admission_id":f"A{pid}",
                        "icd_codes":d["dx"][:4],"medications":d["rx"][:3],
                        "source":"MIMIC-IV demo"},separators=(",",":")).encode()
        recs.append(rec)
    return recs

RECS=build_records(200)
sizes=sorted(len(r) for r in RECS)
print(f"records={len(RECS)} median={statistics.median(sizes):.0f}B min={sizes[0]} max={sizes[-1]}")

# ---------------- EXPERIMENT A: real tamper-to-detection ----------------
def verify_prefix(leaves,k,published_root):
    """verify-before-publish: recompute root over anchored prefix only"""
    return root_of(leaves[:k])==published_root

def exp_A():
    res={}
    for T,trials in [(1.0,40),(5.0,12)]:
        leaves=list(RECS[:100])
        k=len(leaves)
        pub=root_of(leaves[:k]); tsa_publish(pub)
        t_prev=time.monotonic()
        recs=[]
        for t in range(trials):
            frac=rng.random()
            idx=rng.randrange(0,k)
            orig=leaves[idx]
            tamper_at=t_prev+frac*T
            # running workload: legit writes arriving through the interval
            nw=rng.randrange(1,4)
            now=time.monotonic()
            if tamper_at>now: time.sleep(tamper_at-now)
            leaves[idx]=orig+b"|tampered"          # Capability B: bytes changed
            target=t_prev+T                         # next scheduled verification
            now=time.monotonic()
            if target>now: time.sleep(target-now)
            t_d0=time.monotonic()
            ok=verify_prefix(leaves,k,pub)          # the detection step
            t_d1=time.monotonic()
            detected = not ok
            delay=(t_d1-tamper_at)*1000.0
            recs.append({"T":T,"frac":frac,"idx":idx,"detected":detected,
                         "delay_ms":delay,"verify_ms":(t_d1-t_d0)*1000.0})
            # repair + honest republish (exercises the legitimate path too)
            leaves[idx]=orig
            for j in range(nw):
                leaves.append(RECS[(t+j)%len(RECS)]+f"|w{t}".encode())
            k=len(leaves); pub=root_of(leaves[:k])
            tsa_publish(pub)
            t_prev=time.monotonic()
        ds=[r["delay_ms"] for r in recs if r["detected"]]
        res[str(T)]={"trials":len(recs),"detected":sum(r["detected"] for r in recs),
            "mean_ms":statistics.mean(ds),"median_ms":statistics.median(ds),
            "min_ms":min(ds),"max_ms":max(ds),
            "p95_ms":sorted(ds)[int(.95*len(ds))-1],
            "theory_mean_ms":T*1000/2,"theory_worst_ms":T*1000,
            "verify_ms_median":statistics.median(r["verify_ms"] for r in recs)}
        print(f"A T={T}s trials={len(recs)} detected={res[str(T)]['detected']} "
              f"mean={res[str(T)]['mean_ms']:.1f}ms median={res[str(T)]['median_ms']:.1f}ms "
              f"min={res[str(T)]['min_ms']:.1f} max={res[str(T)]['max_ms']:.1f} "
              f"(theory T/2={T*1000/2:.0f})")
    return res

# ---------------- EXPERIMENT B: race counts + localization ----------------
BLOCK=64
def exp_B():
    k=1000
    leaves=[RECS[i%len(RECS)]+f"#{i}".encode() for i in range(k)]
    held_levels=build_levels([lhash(x) for x in leaves])
    pub=held_levels[-1][0]
    nblocks=(k+BLOCK-1)//BLOCK
    block_roots=[root_of([lhash(x) for x in leaves[i*BLOCK:(i+1)*BLOCK]]) for i in range(nblocks)]

    def caught(pos):
        lv=list(leaves); lv[pos]=lv[pos]+b"|tampered"
        return not verify_prefix(lv,k,pub)

    zones={"early(0-10%)":range(0,int(.1*k)),"middle(45-55%)":range(int(.45*k),int(.55*k)),
           "tail(90-100%)":range(int(.9*k),k)}
    zres={}
    for name,rng_ in zones.items():
        pos=list(rng_); got=sum(caught(p) for p in pos)
        zres[name]={"positions":len(pos),"caught":got,"rate":got/len(pos)}
        print(f"B {name}: {got}/{len(pos)} caught")

    # suffix (post-anchor) tampering: correctly treated as legitimate new data?
    suffix=[RECS[i%len(RECS)]+f"|s{i}".encode() for i in range(200)]
    lv=leaves+suffix
    # tamper a suffix record BEFORE it is ever anchored
    lv[k+50]=lv[k+50]+b"|tampered"
    suffix_laundered = verify_prefix(lv,k,pub)   # prefix check passes
    print(f"B suffix tamper laundered (prefix check passes) = {suffix_laundered}")

    # localization with block-level anchoring
    tampered=[37,400,999]
    lv=list(leaves)
    for p in tampered: lv[p]=lv[p]+b"|tampered"
    mism=[i for i in range(nblocks)
          if root_of([lhash(x) for x in lv[i*BLOCK:(i+1)*BLOCK]])!=block_roots[i]]
    comps=[]
    for b in mism:
        held=build_levels([lhash(x) for x in leaves[b*BLOCK:(b+1)*BLOCK]])
        new=build_levels([lhash(x) for x in lv[b*BLOCK:(b+1)*BLOCK]])
        idx=0
        if held[-1][0]!=new[-1][0]:
            for d in range(len(held)-1,0,-1):
                L=idx*2
                if L<len(held[d-1]) and held[d-1][L]!=new[d-1][L]: idx=L
                else: idx=L+1
        comps.append(idx)
    found=[b*BLOCK+comps[i] for i,b in enumerate(mism)]
    print(f"B blocks flagged={len(mism)} localized={found} exact={found==tampered} "
          f"comparisons_per_block={len(build_levels([lhash(b'x')]*BLOCK))-1}")
    # single-root design cannot localize
    print("B single-root design: cannot attribute a prefix mismatch to a record (no per-record evidence held)")
    return {"zones":zres,"suffix_laundered":suffix_laundered,
            "tampered":tampered,"flagged_blocks":len(mism),"localized":found,
            "exact":found==tampered,
            "comparisons_per_block":len(build_levels([lhash(b'x')]*BLOCK))-1,
            "nblocks":nblocks}

A=exp_A(); B=exp_B()
(OUT/"stage1.json").write_text(json.dumps({"seed":SEED,"A":A,"B":B,
   "record_sizes":{"median":statistics.median(sizes),"min":sizes[0],"max":sizes[-1]}},indent=2))
print("saved",OUT/"stage1.json")
