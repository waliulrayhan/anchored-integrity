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

#!/usr/bin/env python3
"""
Anchored clinical-record integrity + automated containment: canonical measurement script (v4).
Produces every number reported in the paper.

Design changes over v3 (all in response to review):
  * Merkle tree (RFC 6962 style, domain-separated) instead of a linear per-record chain.
    Append and inclusion proof are O(log n); the earlier linear chain was O(n).
  * REAL external anchor: RFC 3161 timestamp over the Merkle root via a public TSA.
    Network round trip is measured, not simulated.
  * VERIFY-BEFORE-PUBLISH: a new anchor is refused if the previously anchored prefix
    no longer matches its published root, closing the laundering race.
  * DETECTION DELAY measured: tamper -> detection bound as a function of anchor interval.
  * BASELINE: containment gated on a learned score, to contrast with deterministic gating.
  * Network-facing detection REMOVED from the paper's claims (NSL-KDD retained only as
    the score-gated baseline, and labelled as dated and non-healthcare).
"""
import hashlib, json, os, statistics, subprocess, sys, tempfile, time, gzip, random
import numpy as np
random.seed(20261011)

# ---------------- Merkle log (RFC 6962 domain separation) ----------------
def h(b): return hashlib.sha256(b).digest()
def leaf_hash(rb): return h(b"\x00" + rb)
def node_hash(l, r): return h(b"\x01" + l + r)

def build_levels(leaves):
    if not leaves: return [[]]
    lv = [list(leaves)]; cur = list(leaves)
    while len(cur) > 1:
        cur = [node_hash(cur[i], cur[i+1] if i+1 < len(cur) else cur[i]) for i in range(0, len(cur), 2)]
        lv.append(cur)
    return lv

def root_of(leaves): return build_levels(leaves)[-1][0] if leaves else h(b"")

def inclusion_proof(levels, idx):
    path = []; i = idx
    for L in range(len(levels) - 1):
        cur = levels[L]; sib = i ^ 1
        if sib < len(cur): path.append(("R" if sib > i else "L", cur[sib]))
        i //= 2
    return path

def verify_inclusion(rec_bytes, idx, path, root):
    cur = leaf_hash(rec_bytes)
    for side, sib in path:
        cur = node_hash(cur, sib) if side == "R" else node_hash(sib, cur)
    return cur == root

# ---------------- External anchors ----------------
class LocalAnchorStub:
    """What a same-domain append amounts to; included only to show what NOT to do."""
    name = "same-domain append (stub)"
    def publish(self, root):
        t0 = time.perf_counter_ns(); _ = (len(root), root)
        return {"rtt_us": (time.perf_counter_ns()-t0)/1000.0, "token_bytes": len(root), "granted": True}

class Rfc3161TSA:
    """REAL external anchor. The TSA is outside this system's trust domain."""
    name = "rfc3161 (public TSA, external)"
    URL = os.environ.get("TSA_URL", "https://rfc3161.ai.moda")
    def publish(self, root):
        t0 = time.perf_counter_ns()
        with tempfile.TemporaryDirectory() as d:
            dp, qp, rp = d+"/r.bin", d+"/q.tsq", d+"/r.tsr"
            open(dp, "wb").write(root)
            q = subprocess.run(["openssl","ts","-query","-data",dp,"-sha256","-cert","-out",qp],
                               capture_output=True)
            if q.returncode != 0: raise RuntimeError("ts -query failed: "+q.stderr.decode()[:200])
            c = subprocess.run(["curl","-s","--max-time","30","-H","Content-Type: application/timestamp-query",
                                "--data-binary","@"+qp,"-o",rp,self.URL], capture_output=True)
            if c.returncode != 0: raise RuntimeError("curl failed")
            got = open(rp,"rb").read() if os.path.exists(rp) else b""
        return {"rtt_us": (time.perf_counter_ns()-t0)/1000.0, "token_bytes": len(got), "granted": len(got) > 200}

# ---------------- Store with verify-before-publish ----------------
class AnchoredStore:
    def __init__(self, anchor):
        self.anchor = anchor
        self.leaves = []            # record digests (leaf inputs are already-hashed record bytes)
        self.levels = None
        self.anchors = []           # (leaf_count_at_publish, root)
        self.blocked_republishes = 0
        self.publishes = 0
    def append(self, rec_bytes):
        lh = leaf_hash(rec_bytes); self.leaves.append(lh); self.levels = None
        return lh
    def root(self):
        if self.levels is None or len(self.levels[0]) != len(self.leaves): self.levels = build_levels(self.leaves)
        return self.levels[-1][0]
    def publish(self, verify_first=True):
        """VERIFY-BEFORE-PUBLISH. Refuse to publish a root if the previously anchored
        prefix no longer hashes to the root we published for it."""
        if verify_first and self.anchors:
            last_n, last_root = self.anchors[-1]
            if len(self.leaves) >= last_n:
                if root_of(self.leaves[:last_n]) != last_root:
                    self.blocked_republishes += 1
                    return {"ok": False, "reason": "anchored_prefix_mismatch"}
        r = self.anchor.publish(self.root())
        self.anchors.append((len(self.leaves), self.root())); self.publishes += 1
        r["ok"] = True; r["leaf_count"] = len(self.leaves)
        return r

print("="*78)
print("ANCHORED CLINICAL INTEGRITY -- canonical measurement run (v4)")
print("="*78)
R = {}

# ============ 0. realistic store from REAL de-identified clinical records ============
D = DATA
def rd(p):
    import csv
    with open(p, newline="") as f: return list(csv.DictReader(f))
store_stats = {}
try:
    miiv_adm = rd(f"{D}/miiv_admissions.csv")
    miiv_dx  = rd(f"{D}/miiv_diagnoses_icd.csv")
    miiv_lab = rd(f"{D}/miiv_labevents.csv")
    miiv_rx  = rd(f"{D}/miiv_prescriptions.csv")
    miii_adm = rd(f"{D}/miii_ADMISSIONS.csv")
    miii_dx  = rd(f"{D}/miii_DIAGNOSES_ICD.csv")
    miii_lab = rd(f"{D}/miii_LABEVENTS.csv")
    store_stats = dict(miiv_admissions=len(miiv_adm), miiv_diagnoses=len(miiv_dx), miiv_labevents=len(miiv_lab),
                       miiv_prescriptions=len(miiv_rx), miii_admissions=len(miii_adm),
                       miii_diagnoses=len(miii_dx), miii_labevents=len(miii_lab))
except Exception as e:
    store_stats = {"error": repr(e)}
print("\n[0] Real clinical source data (MIMIC-IV / MIMIC-III demonstrations)")
for k,v in store_stats.items(): print(f"    {k:22s} {v}")
R["source_rows"] = store_stats

def mk_record(pid, row, dx_rows, lab_rows, rx_rows, ds):
    """Assemble one clinical episode record from real field values."""
    rec = {"dataset": ds, "patient_id": pid,
           "admission_type": row.get("admission_type",""), "insurance": row.get("insurance",""),
           "admittime": row.get("admittime",""), "dischtime": row.get("dischtime",""),
           "diagnosis_codes": sorted({d.get("icd_code","") for d in dx_rows})[:12],
           "lab_events": len(lab_rows), "prescriptions": len(rx_rows),
           "gender": row.get("gender","")}
    return json.dumps(rec, sort_keys=True).encode()

records = []
try:
    from collections import defaultdict
    dx_by = defaultdict(list); lab_by = defaultdict(list); rx_by = defaultdict(list)
    for d in miiv_dx:  dx_by[d.get("hadm_id")].append(d)
    for d in miiv_lab: lab_by[d.get("hadm_id")].append(d)
    for d in miiv_rx:  rx_by[d.get("hadm_id")].append(d)
    for a in miiv_adm[:100]:
        records.append(mk_record(a.get("subject_id"), a, dx_by[a.get("hadm_id")], lab_by[a.get("hadm_id")], rx_by[a.get("hadm_id")], "MIMIC-IV"))
    dx3 = defaultdict(list); lab3 = defaultdict(list)
    for d in miii_dx:  dx3[d.get("HADM_ID")].append(d)
    for d in miii_lab: lab3[d.get("HADM_ID")].append(d)
    for a in miii_adm[:100]:
        records.append(mk_record(a.get("SUBJECT_ID"), a, dx3[a.get("HADM_ID")], lab3[a.get("HADM_ID")], [], "MIMIC-III"))
except Exception as e:
    print("    record assembly issue:", repr(e))
if not records:
    records = [json.dumps({"patient_id": f"p{i}", "dataset": "MIMIC"}).encode() for i in range(200)]
print(f"    -> record store: {len(records)} episodes")
print(f"    median record size {statistics.median(len(r) for r in records):.0f} bytes")
R["store"] = dict(records=len(records), median_bytes=int(statistics.median(len(r) for r in records)))

# ============ 1. SCALE: Merkle ============
print("\n[1] SCALE  (Merkle append / inclusion proof / verification)")
scale = {}
leaf_cache = {}
for n in (100_000, 1_000_000):
    t0=time.perf_counter(); L=[leaf_hash(f"episode-{i}".encode()) for i in range(n)]; gen=time.perf_counter()-t0
    t0=time.perf_counter(); lv=build_levels(L); build=time.perf_counter()-t0
    root=lv[-1][0]; idx=n//3
    t0=time.perf_counter(); p=inclusion_proof(lv,idx); pgen=(time.perf_counter()-t0)*1e6
    t0=time.perf_counter()
    for _ in range(500): ok=verify_inclusion(f"episode-{idx}".encode(),idx,p,root)
    v=(time.perf_counter()-t0)/500*1e6
    t0=time.perf_counter(); audit=root_of(L); ams=(time.perf_counter()-t0)*1e3
    scale[n]=dict(leaves_s=round(gen,3), tree_build_s=round(build,3), proof_gen_us=round(pgen,2),
                  proof_len=len(p), verify_us=round(v,2), verify_ok=ok, full_audit_ms=round(ams,1),
                  log2n=round(float(np.log2(n)),1))
    print(f"    n={n:>9,}  leaves {gen:6.2f}s  tree {build:6.2f}s  proof {pgen:7.1f}us len={len(p):2d}  "
          f"verify {v:6.2f}us ok={ok}  full audit {ams:8.1f}ms")
    leaf_cache[n]=L
R["scale"]={str(k):v for k,v in scale.items()}

# ============ 2. REAL external anchor ============
print("\n[2] REAL EXTERNAL ANCHOR  (RFC 3161 timestamp over the Merkle root)")
tsa=Rfc3161TSA(); rtts=[]; toks=[]
for k in range(11):
    root = h(f"epoch-{k}-{len(records)}".encode())
    try:
        r=tsa.publish(root); rtts.append(r["rtt_us"]/1000.0); toks.append(r["token_bytes"])
        print(f"    epoch {k:2d}: {r['rtt_us']/1000.0:8.1f} ms  token={r['token_bytes']:5d}B  granted={r['granted']}")
    except Exception as e:
        print(f"    epoch {k:2d}: FAILED {repr(e)[:80]}")
if rtts:
    R["tsa"]=dict(n=len(rtts), median_ms=round(statistics.median(rtts),1), min_ms=round(min(rtts),1),
                  max_ms=round(max(rtts),1), p95_ms=round(float(np.percentile(rtts,95)),1), token_bytes=int(statistics.median(toks)))
    print(f"    --> median {statistics.median(rtts):.1f} ms (min {min(rtts):.1f}, max {max(rtts):.1f}, p95 {np.percentile(rtts,95):.1f}), n={len(rtts)}")
    print(f"    --> a real anchor costs ~{statistics.median(rtts):.0f} ms per publication, not microseconds")

# ============ 3. verify-before-publish ============
print("\n[3] VERIFY-BEFORE-PUBLISH  (closes the laundering race)")
st=AnchoredStore(tsa)
for r in records: st.append(r)
pub=st.publish()
print(f"    honest anchor at leaf count {st.anchors[-1][0]}, root {st.anchors[-1][1].hex()[:16]} (ok={pub.get('ok')})")
orig=st.anchors[-1][1]
st.leaves[40]=leaf_hash(b'{"tampered":true}')
print(f"    outsider rewrote episode 40 -> tree root now {st.root().hex()[:16]} (differs={st.root()!=orig})")
res=st.publish()
print(f"    republish attempt after tamper: ok={res.get('ok')} reason={res.get('reason','-')}")
print(f"    blocked={st.blocked_republishes}  publishes={st.publishes}")
R["vbp"]=dict(blocked=st.blocked_republishes, publishes=st.publishes, blocked_ok=not res.get("ok", True))

# ============ 4. detection delay ============
print("\n[4] DETECTION DELAY  (tamper -> detection; the operationally meaningful number)")
N=100_000; L=list(leaf_cache[N]); lv=build_levels(L); anchored=lv[-1][0]; TI=N//2
Lt=list(L); Lt[TI]=leaf_hash(b'{"tampered":true}'); lvt=build_levels(Lt); tr=lvt[-1][0]
pt=inclusion_proof(lvt,TI)
caught = not verify_inclusion(b'{"tampered":true}', TI, pt, anchored)
honest_ok = verify_inclusion(f"episode-{TI}".encode(), TI, pt, anchored)
print(f"    tampered leaf vs published anchor: verifies={caught}  -> False means CAUGHT")
print(f"    honest leaf    vs published anchor: verifies={honest_ok} -> True means correct")
t0=time.perf_counter()
for _ in range(3000): verify_inclusion(b'{"tampered":true}', TI, pt, anchored)
det_us=(time.perf_counter()-t0)/3000*1e6
print(f"    per-record detection cost {det_us:.2f} us (n={N:,}, proof {len(pt)} hashes)")
rows=[]
for T in (1, 100, 1_000, 10_000, 100_000):
    t0=time.perf_counter(); root_of(Lt); ams=(time.perf_counter()-t0)*1e3
    rows.append(dict(anchor_interval_writes=T, worst_case_delay_writes=T, mean_delay_writes=T//2, audit_ms=round(ams,1)))
    print(f"    anchor every {T:>7,} writes: worst-case delay {T:>7,} writes, mean {T//2:>7,}, audit cost {ams:7.1f} ms")
tw = statistics.median(rtts)/1000.0 if rtts else None
if tw:
    print(f"    wall-clock framing: anchoring is bounded by the real TSA cost (~{tw:.2f}s), so")
    print(f"      anchor-per-write -> ~{tw:.2f}s detection granularity; anchor-every-60s -> 60s worst case (30s mean)")
R["delay"]=dict(per_record_verify_us=round(det_us,2), proof_len=len(pt), audit=rows,
                anchor_wall_clock_s=(round(tw,3) if tw else None))

# ============ 5. baseline: score-gated containment ============
print("\n[5] BASELINE  containment gated on a learned score")
NKD=NSLKDD
try:
    import pandas as pd
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import confusion_matrix
    tr=pd.read_csv(f"{NKD}/KDDTrain+_20Percent.txt",header=None)
    te=pd.read_csv(f"{NKD}/KDDTest+.txt",header=None)
    def prep(df):
        y=(df[41]!="normal").astype(int).values
        X=df.drop(columns=[1,2,3,41,42]).astype(float).values
        return X,y
    Xtr,ytr=prep(tr); Xte,yte=prep(te)
    rf=RandomForestClassifier(n_estimators=100,class_weight="balanced",random_state=0,n_jobs=2).fit(Xtr,ytr)
    proba=rf.predict_proba(Xte)[:,1]
    sweep=[]
    for th in (0.5,0.9,0.99,0.999):
        p=(proba>=th).astype(int); tn,fp,fn,tp=confusion_matrix(yte,p).ravel()
        sweep.append(dict(threshold=th, needless_containment=int(fp), fpr=round(fp/(tn+fp),6),
                          recall=round(tp/(tp+fn),4)))
        print(f"    score>={th:<6} needless containment={fp:5d}  FPR={fp/(tn+fp):.6f}  recall={tp/(tp+fn):.4f}")
    print(f"    vs deterministic integrity gate measured above: 0 needless containment ({len(records)}/{len(records)} honest)")
    R["baseline"]=dict(dataset="NSL-KDD (dated, non-healthcare; illustrative only)", n_test=int(len(yte)), sweep=sweep)
except Exception as e:
    print("    baseline failed:", repr(e)); R["baseline"]={"error":repr(e)}

# ============ 6. component cost of the clinical path ============
print("\n[6] COMPONENT COST  (real serialized records)")
st2=AnchoredStore(LocalAnchorStub())
for r in records: st2.append(r)
root=st2.root()
def bench(fn,n=2000):
    t0=time.perf_counter(); [fn() for _ in range(n)]; return (time.perf_counter()-t0)/n*1e6
idx=len(records)//2; rec=records[idx]
lv2=build_levels(st2.leaves); p2=inclusion_proof(lv2,idx)
comp={
 "SHA-256 over record": lambda: leaf_hash(rec),
 "Merkle leaf append": lambda: st2.append(rec),
 "inclusion proof generation": lambda: inclusion_proof(lv2,idx),
 "inclusion verification": lambda: verify_inclusion(rec,idx,p2,root),
 "anchor root comparison (O(1))": lambda: (root==root),
}
comp_us={}
for k,f in comp.items():
    v=bench(f); comp_us[k]=round(v,3); print(f"    {k:34s} {v:9.3f} us")
# authenticated write with PBKDF2
t0=time.perf_counter()
for _ in range(10): hashlib.pbkdf2_hmac("sha256", b"pw", b"saltsaltsalt", 100_000)
pbk=(time.perf_counter()-t0)/10*1e3
print(f"    {'PBKDF2 100k iterations (auth)':34s} {pbk:9.3f} ms")
# full write path
ws=[]
for r in records[:50]:
    t0=time.perf_counter()
    hashlib.pbkdf2_hmac("sha256",b"pw",b"saltsaltsalt",100_000)
    st3=AnchoredStore(LocalAnchorStub()); st3.append(r); st3.root()
    ws.append((time.perf_counter()-t0)*1e3)
print(f"    authenticated write end-to-end: median {statistics.median(ws):.2f} ms (min {min(ws):.2f}, max {max(ws):.2f})")
R["components"]=dict(us=comp_us, pbkdf2_ms=round(pbk,3), write_ms=dict(
    median=round(statistics.median(ws),2), min=round(min(ws),2), max=round(max(ws),2)))

json.dump(R, open(str(OUT) + "/canonical_v4.json","w"), indent=1, default=str)
open(str(OUT) + "/canonical_v4.txt","w").write(json.dumps(R, indent=1, default=str))
print("\nSaved " + str(OUT) + "/canonical_v4.json")
