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

import hashlib,json,random,statistics,time,subprocess,tempfile
from pathlib import Path
OUT=Path(OUT); TMP=Path(tempfile.mkdtemp(dir=WORK))
SEED=20261011; rng=random.Random(SEED)
def h(b): return hashlib.sha256(b).digest()
def lh(x): return h(b"\x00"+x)                 # RFC 6962 leaf
def nh(l,r): return h(b"\x01"+l+r)             # RFC 6962 node
def levels(leafhashes):
    lv=[list(leafhashes)]
    while len(lv[-1])>1:
        cur=lv[-1]; nxt=[]
        for i in range(0,len(cur),2):
            nxt.append(nh(cur[i],cur[i+1]) if i+1<len(cur) else cur[i])  # RFC 6962 promotion
        lv.append(nxt)
    return lv
def root(records): return levels([lh(x) for x in records])[-1][0] if records else h(b"")

TSA="https://rfc3161.ai.moda"
def anchor(r):   # real TSA round trip
    d=TMP/"d"; q=TMP/"q"; s=TMP/"r"; d.write_bytes(r)
    subprocess.run(["openssl","ts","-query","-data",str(d),"-sha256","-cert","-out",str(q)],check=True,capture_output=True)
    t0=time.perf_counter()
    subprocess.run(["curl","-s","-m","20","-H","Content-Type: application/timestamp-query",
                    "--data-binary",f"@{q}",TSA,"-o",str(s)],capture_output=True)
    return (time.perf_counter()-t0)*1000.0

def recs(n,base=0): return [f"record-{base+i}".encode() for i in range(n)]

# ---------- self-test: the harness must be able to tell tampered from honest ----------
def selftest():
    R=recs(500); pub=root(R)
    assert root(R)==pub,                       "honest prefix must verify"
    T=list(R); T[10]=T[10]+b"|x"
    assert root(T)!=pub,                       "prefix tamper must be detected"
    # suffix-only tamper: prefix root unchanged
    pre=R[:200]; pubs=root(pre)
    withsuffix=pre+recs(50,base=900)
    ws=list(withsuffix); ws[230]=ws[230]+b"|x"   # tamper inside the SUFFIX
    assert root(ws[:200])==pubs,               "suffix tamper must NOT disturb prefix root"
    print("SELFTEST ok: honest verifies, prefix tamper caught, suffix tamper does not disturb prefix")
    return True
st=selftest()

# ---------- A: real injected tamper-to-detection under running workload ----------
def exp_A():
    out={}
    for T,trials in [(1.0,40),(5.0,12)]:
        R=recs(120); k=len(R); pub=root(R[:k]); anchor(pub)
        t_ref=time.monotonic(); rows=[]
        for t in range(trials):
            idx=rng.randrange(0,k)
            offset=rng.random()                       # where in the interval the tamper lands
            tamper_at=t_ref+offset*T
            nw=rng.randrange(1,4)
            now=time.monotonic()
            if tamper_at>now: time.sleep(tamper_at-now)
            orig=R[idx]
            R[idx]=orig+b"|tampered"                   # Capability B
            due=t_ref+T                                # next scheduled verification
            now=time.monotonic()
            if due>now: time.sleep(due-now)
            t0=time.perf_counter(); ok=(root(R[:k])==pub); vms=(time.perf_counter()-t0)*1000
            detected = not ok
            rows.append({"tamper_at":tamper_at,"detected":detected,
                         "delay_ms":(time.monotonic()-tamper_at)*1000,"verify_ms":vms})
            assert detected, "injected tamper inside the anchored prefix must be detected"
            R[idx]=orig                                # repair
            for j in range(nw): R.append(recs(1,base=1000+t*10+j)[0])
            k=len(R); pub=root(R[:k]); anchor(pub)
            t_ref=time.monotonic()
        ds=[r["delay_ms"] for r in rows]
        out[str(T)]={"trials":len(rows),"detected":sum(r["detected"] for r in rows),
          "mean_ms":statistics.mean(ds),"median_ms":statistics.median(ds),
          "min_ms":min(ds),"max_ms":max(ds),"p95_ms":sorted(ds)[int(.95*len(ds))-1],
          "formula_mean_ms":T*1000/2,"formula_worst_ms":T*1000,
          "verify_ms_median":statistics.median(r["verify_ms"] for r in rows)}
        o=out[str(T)]
        print(f"A T={T}s det={o['detected']}/{o['trials']} mean={o['mean_ms']:.1f} median={o['median_ms']:.1f} "
              f"min={o['min_ms']:.1f} max={o['max_ms']:.1f} (formula T/2={o['formula_mean_ms']:.0f})")
    return out

# ---------- B: race counts by tamper position + suffix behaviour + localization ----------
def exp_B():
    k=1000
    R=recs(k); pub=root(R); anchor(pub)
    zones={"early (positions 0-99)":range(0,100),
           "middle (450-549)":range(450,550),
           "tail (900-999)":range(900,1000)}
    zres={}
    for name,rr in zones.items():
        pos=list(rr); caught=0
        for p in pos:
            T=list(R); T[p]=T[p]+b"|tampered"
            if root(T)!=pub: caught+=1
        zres[name]={"positions":len(pos),"caught":caught,"rate":caught/len(pos)}
        print(f"B {name}: caught {caught}/{len(pos)}")
    # suffix-only tampering => legitimately invisible to the prefix check
    suffix=recs(200,base=5000)
    lv=R+suffix; lv=list(lv); lv[k+50]=lv[k+50]+b"|tampered"
    prefix_ok = (root(lv[:k])==pub)
    print(f"B suffix-only tamper: prefix check passes={prefix_ok} (expected True -> unprotected window)")
    # localization via block-level subtrees
    B=64; nb=(k+B-1)//B
    broots=[root(R[i*B:(i+1)*B]) for i in range(nb)]
    tampered=[37,400,999]; lv=list(R)
    for p in tampered: lv[p]=lv[p]+b"|tampered"
    mism=[i for i in range(nb) if root(lv[i*B:(i+1)*B])!=broots[i]]
    loc=[]
    for b in mism:
        seg_old=R[b*B:(b+1)*B]; seg_new=lv[b*B:(b+1)*B]
        idx=0; d=len(levels([lh(x) for x in seg_old]))-1
        while d>0:
            L=idx*2
            ol=levels([lh(x) for x in seg_old])[d-1]; nw=levels([lh(x) for x in seg_new])[d-1]
            idx = L if (L<len(ol) and ol[L]!=nw[L]) else L+1
            d-=1
        loc.append(b*B+idx)
    print(f"B localization: {len(mism)} blocks flagged, localized={loc} exact={loc==tampered} "
          f"(log2 block = {(B).bit_length()-1} comparisons)")
    return {"zones":zres,"suffix_prefix_check_passes":prefix_ok,
            "tampered":tampered,"blocks_flagged":len(mism),"localized":loc,"exact":loc==tampered,
            "block_size":B,"comparisons_per_block":(B).bit_length()-1,"nblocks":nb}

A=exp_A(); B=exp_B()
json.dump({"seed":SEED,"selftest":st,"A":A,"B":B},open(OUT/"stage4.json","w"),indent=2)
print("saved",flush=True)
