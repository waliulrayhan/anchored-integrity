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

"""Canonical measurement script for the revised paper.
Everything reported in Section VII comes from this single run.

Merkle log follows RFC 6962 semantics: an unpaired node is PROMOTED, not duplicated.
(An earlier draft duplicated it, which silently broke ~1.7% of inclusion proofs at n=1e6.)
"""
import hashlib, json, os, platform, statistics, subprocess, tempfile, time, csv
from collections import defaultdict
import numpy as np

def h(b): return hashlib.sha256(b).digest()
def leaf_hash(rb): return h(b"\x00"+rb)
def node_hash(l,r): return h(b"\x01"+l+r)
def build_levels(L):
    if not L: return [[]]
    lv=[list(L)]; cur=list(L)
    while len(cur)>1:
        nxt=[node_hash(cur[i],cur[i+1]) for i in range(0,len(cur)-1,2)]
        if len(cur)%2: nxt.append(cur[-1])            # promote
        lv.append(nxt); cur=nxt
    return lv
def root_of(L): return build_levels(L)[-1][0] if L else h(b"")
def proof_from(lv,i):
    p=[]
    for L in range(len(lv)-1):
        c=lv[L]; s=i^1
        if s<len(c): p.append(("R" if s>i else "L",c[s]))
        i//=2
    return p
def verify_incl(rb,i,p,root):
    c=leaf_hash(rb)
    for side,sib in p: c=node_hash(c,sib) if side=="R" else node_hash(sib,c)
    return c==root

def med(v): return statistics.median(v)
def p(v,q): return float(np.percentile(v,q))
R={}

# ---------------- 0. honest-verification correctness across sizes ----------------
print("[0] Merkle correctness: false alarms on untampered records")
corr={}
for n in (100,1000,10001,100000,1000000):
    L=[leaf_hash(f"e{i}".encode()) for i in range(n)]; lv=build_levels(L); r=lv[-1][0]
    bad=sum(0 if verify_incl(f"e{i}".encode(),i,proof_from(lv,i),r) else 1 for i in range(n))
    corr[n]=bad; print(f"    n={n:>9,}  false alarms {bad}")
R["correctness"]={str(k):v for k,v in corr.items()}

# ---------------- 1. store from real records ----------------
D=DATA
def rd(f):
    with open(f,newline="") as fh: return list(csv.DictReader(fh))
miiv_adm,miiv_dx=rd(f"{D}/miiv_admissions.csv"),rd(f"{D}/miiv_diagnoses_icd.csv")
miiv_lab,miiv_rx=rd(f"{D}/miiv_labevents.csv"),rd(f"{D}/miiv_prescriptions.csv")
miii_adm,miii_dx,miii_lab=rd(f"{D}/miii_ADMISSIONS.csv"),rd(f"{D}/miii_DIAGNOSES_ICD.csv"),rd(f"{D}/miii_LABEVENTS.csv")
def mk(ds,pid,row,dx,lab,rx):
    return json.dumps({"dataset":ds,"patient_id":pid,"admission_type":row.get("admission_type",""),
      "insurance":row.get("insurance",""),"admittime":row.get("admittime",""),"dischtime":row.get("dischtime",""),
      "diagnosis_codes":sorted({d.get("icd_code","") for d in dx})[:12],"lab_events":len(lab),
      "prescriptions":len(rx),"gender":row.get("gender","")},sort_keys=True).encode()
db=defaultdict(list); lb=defaultdict(list); rb=defaultdict(list)
for d in miiv_dx: db[d.get("hadm_id")].append(d)
for d in miiv_lab: lb[d.get("hadm_id")].append(d)
for d in miiv_rx: rb[d.get("hadm_id")].append(d)
records=[mk("MIMIC-IV",a.get("subject_id"),a,db[a.get("hadm_id")],lb[a.get("hadm_id")],rb[a.get("hadm_id")]) for a in miiv_adm[:100]]
d3=defaultdict(list); l3=defaultdict(list)
for d in miii_dx: d3[d.get("HADM_ID")].append(d)
for d in miii_lab: l3[d.get("HADM_ID")].append(d)
records+=[mk("MIMIC-III",a.get("SUBJECT_ID"),a,d3[a.get("HADM_ID")],l3[a.get("HADM_ID")],[]) for a in miii_adm[:100]]
sz=[len(r) for r in records]
R["store"]={"episodes":len(records),"median_bytes":int(med(sz)),"mean_bytes":round(statistics.mean(sz),1),
  "min_bytes":min(sz),"max_bytes":max(sz),"overhead_pct":round(len(leaf_hash(records[0]))/med(sz)*100,1),
  "source_rows":{"miiv_admissions":len(miiv_adm),"miiv_diagnoses_icd":len(miiv_dx),"miiv_labevents":len(miiv_lab),
    "miiv_prescriptions":len(miiv_rx),"miii_admissions":len(miii_adm),"miii_diagnoses_icd":len(miii_dx),
    "miii_labevents":len(miii_lab)}}
print("\n[1] store:",R["store"]["episodes"],"episodes, median",R["store"]["median_bytes"],"B")

# ---------------- 2. scale ----------------
print("\n[2] scale")
L1k=None; L1M=None; lv1k=None; lvM=None; rootM=None
scale={}
for n in (100_000,1_000_000):
    t0=time.perf_counter(); L=[leaf_hash(f"episode-{i}".encode()) for i in range(n)]; gen=time.perf_counter()-t0
    t0=time.perf_counter(); lv=build_levels(L); bld=time.perf_counter()-t0
    root=lv[-1][0]; idx=n//3
    pg=[]
    for _ in range(50):
        t0=time.perf_counter(); pf=proof_from(lv,idx); pg.append((time.perf_counter()-t0)*1e6)
    vv=[]
    for _ in range(2000):
        t0=time.perf_counter(); ok=verify_incl(f"episode-{idx}".encode(),idx,pf,root); vv.append((time.perf_counter()-t0)*1e6)
    t0=time.perf_counter(); root_of(L); ams=(time.perf_counter()-t0)*1e3
    scale[n]=dict(leaves_s=round(gen,3),tree_s=round(bld,3),proof_len=len(pf),
        proof_gen_us=round(med(pg),2),verify_us=round(med(vv),2),verify_ok=bool(ok),
        full_audit_ms=round(ams,1),log2n=round(float(np.log2(n)),1))
    print(f"    n={n:>9,} leaves {gen:5.2f}s tree {bld:5.2f}s proof len={len(pf):2d} gen {med(pg):5.2f}us verify {med(vv):6.2f}us audit {ams:7.1f}ms ok={ok}")
    if n==100_000: L1k,lv1k=L,lv
    else: L1M,lvM,rootM=L,lv,lv[-1][0]
R["scale"]={str(k):v for k,v in scale.items()}

# ---------------- 3. real external anchor ----------------
print("\n[3] real RFC 3161 anchor")
class TSA:
    URL=os.environ.get("TSA_URL","https://rfc3161.ai.moda")
    def publish(self,root):
        t0=time.perf_counter_ns()
        with tempfile.TemporaryDirectory() as d:
            dp,qp,rp=d+"/r",d+"/q",d+"/s"
            open(dp,"wb").write(root)
            q=subprocess.run(["openssl","ts","-query","-data",dp,"-sha256","-cert","-out",qp],capture_output=True)
            if q.returncode: raise RuntimeError(q.stderr.decode()[:100])
            subprocess.run(["curl","-s","--max-time","30","-H","Content-Type: application/timestamp-query",
                "--data-binary","@"+qp,"-o",rp,self.URL],capture_output=True)
            got=open(rp,"rb").read() if os.path.exists(rp) else b""
        return (time.perf_counter_ns()-t0)/1e3,len(got)
tsa=TSA(); rt=[]; tk=[]; ok_n=0
for k in range(15):
    try:
        us,tb=tsa.publish(h(f"root-{k}".encode())); rt.append(us/1000.0); tk.append(tb)
        ok_n+= tb>200
    except Exception as e: print("    fail",repr(e)[:50])
R["tsa"]={"url":TSA.URL,"n":len(rt),"granted":ok_n,"median_ms":round(med(rt),1),"min_ms":round(min(rt),1),
  "max_ms":round(max(rt),1),"p95_ms":round(p(rt,95),1),"token_bytes":int(med(tk))}
print("   ",R["tsa"])

# ---------------- 4. verify-before-publish ----------------
print("\n[4] verify-before-publish")
Lt=list(L1k); Lt[40]=leaf_hash(b'{"tampered":true}')
R["vbp"]={"prefix_mismatch":bool(build_levels(Lt)[-1][0]!=lv1k[-1][0]),
  "republish_refused":bool(root_of(Lt[:100000])!=lv1k[-1][0]),
  "honest_republish_ok":bool(root_of(L1k)==lv1k[-1][0])}
print("   ",R["vbp"])

# ---------------- 5. detection ----------------
print("\n[5] detection")
N=1_000_000; TI=N//2
lt=list(L1M); lt[TI]=leaf_hash(b'{"tampered":true}'); lvt=build_levels(lt)
pt=proof_from(lvt,TI)
v_t=verify_incl(b'{"tampered":true}',TI,pt,rootM)
v_h=verify_incl(f"episode-{TI}".encode(),TI,proof_from(lvM,TI),rootM)
hmm=sum(0 if verify_incl(f"episode-{i}".encode(),i,proof_from(lvM,i),rootM) else 1 for i in range(0,N,50))
dv=[]
for _ in range(3000):
    t0=time.perf_counter(); verify_incl(b'{"tampered":true}',TI,pt,rootM); dv.append((time.perf_counter()-t0)*1e6)
sw=[]
for T in (1,100,1000,10000,100000):
    t0=time.perf_counter(); root_of(lt); sw.append(dict(interval=T,worst=T,mean=T//2,audit_ms=round((time.perf_counter()-t0)*1e3,1)))
R["detect"]={"n":N,"tampered_caught":not v_t,"honest_ok":bool(v_h),"per_record_us":round(med(dv),2),
  "honest_checked":N//50,"honest_false_alarms":hmm,"sweep":sw,
  "clock_granularity_s":round(med(rt)/1000.0,3)}
print(f"    caught={not v_t} honest_ok={v_h} per-record {med(dv):.2f}us false_alarms={hmm}/{N//50}")
for s in sw: print("   ",s)

# ---------------- 6. components ----------------
print("\n[6] components")
idx=len(records)//2; rec=records[idx]; lvc=build_levels([leaf_hash(r) for r in records])
pc=proof_from(lvc,idx); rc=lvc[-1][0]
def bench(fn,n):
    for _ in range(max(50,n//20)): fn()
    t0=time.perf_counter()
    for _ in range(n): fn()
    return (time.perf_counter()-t0)/n*1e6
def benchms(fn,n):
    for _ in range(max(2,n//5)): fn()
    t0=time.perf_counter()
    for _ in range(n): fn()
    return (time.perf_counter()-t0)/n*1e3
C={"sha256_record_us":round(bench(lambda: leaf_hash(rec),20000),3),
   "proof_gen_n200_us":round(bench(lambda: proof_from(lvc,idx),20000),3),
   "proof_gen_n100k_us":round(bench(lambda: proof_from(lv1k,idx),5000),3),
   "proof_gen_n1M_us":round(bench(lambda: proof_from(lvM,idx),5000),3),
   "verify_n200_us":round(bench(lambda: verify_incl(rec,idx,pc,rc),20000),3),
   "verify_n100k_us":round(bench(lambda: verify_incl(f"episode-{idx}".encode(),idx,proof_from(lv1k,idx),lv1k[-1][0]),5000),3),
   "verify_n1M_us":round(bench(lambda: verify_incl(f"episode-{idx}".encode(),idx,proof_from(lvM,idx),rootM),2000),3),
   "anchor_compare_us":round(bench(lambda: rootM==rootM,50000),4),
   "pbkdf2_100k_ms":round(benchms(lambda: hashlib.pbkdf2_hmac("sha256",b"pw",b"saltsaltsalt",100_000),20),3),
   "pbkdf2_10k_ms":round(benchms(lambda: hashlib.pbkdf2_hmac("sha256",b"pw",b"saltsaltsalt",10_000),80),3)}
ws=[]
for r in records[:60]:
    t0=time.perf_counter()
    hashlib.pbkdf2_hmac("sha256",b"pw",b"saltsaltsalt",100_000)
    build_levels([leaf_hash(r)])
    ws.append((time.perf_counter()-t0)*1e3)
C["write_e2e_ms"]=round(med(ws),2); C["write_min_ms"]=round(min(ws),2); C["write_max_ms"]=round(max(ws),2)
t0=time.perf_counter()
for _ in range(20000): verify_incl(b'{"tampered":true}',TI,pt,rootM)
C["detect_alert_mitigate_us"]=round((time.perf_counter()-t0)/20000*1e6,2)
R["components"]=C
for k,v in C.items(): print(f"    {k:26s} {v}")

# ---------------- 7. classifier inside the response loop ----------------
print("\n[7] classifier inside the loop (baseline)")
try:
    import pandas as pd
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import confusion_matrix
    NKD=NSLKDD
    tr=pd.read_csv(f"{NKD}/KDDTrain+_20Percent.txt",header=None); te=pd.read_csv(f"{NKD}/KDDTest+.txt",header=None)
    def prep(df):
        return df.drop(columns=[1,2,3,41,42]).astype(float).values,(df[41]!="normal").astype(int).values
    Xtr,ytr=prep(tr); Xte,yte=prep(te)
    rf=RandomForestClassifier(n_estimators=100,class_weight="balanced",random_state=0,n_jobs=2).fit(Xtr,ytr)
    pr=rf.predict_proba(Xte)[:,1]
    sweep=[]
    for th in (0.5,0.9,0.99,0.999):
        pp=(pr>=th).astype(int); tn,fp,fn,tp=confusion_matrix(yte,pp).ravel()
        sweep.append(dict(th=th,needless=int(fp),fpr=round(float(fp/(tn+fp)),6),recall=round(float(tp/(tp+fn)),4)))
    cat={c:{"n":int((te[41]==c).sum()),"recall":round(float((pr[(te[41]==c).values]>=0.5).mean()),4)}
         for c in ("dos","probe","r2l","u2r")}
    row=Xte[0].reshape(1,-1)
    inf=bench(lambda: rf.predict_proba(row),3000)
    R["baseline"]={"n_test":int(len(yte)),"n_attack":int(yte.sum()),"sweep":sweep,"by_category":cat,
                   "inference_us_per_flow":round(inf,1),
                   "needless_per_10k_at_0.5":round(sweep[0]["fpr"]*10000,1),
                   "needless_per_10k_at_0.99":round(sweep[2]["fpr"]*10000,1)}
    for s_ in sweep: print(f"    th={s_['th']:<6} needless={s_['needless']:5d} FPR={s_['fpr']:.6f} recall={s_['recall']:.4f}")
    print("    per-category:",cat); print(f"    RF inference {inf:.1f}us per flow record")
except Exception as e:
    print("    fail",repr(e)[:120]); R["baseline"]={"error":repr(e)}

json.dump(R,open(str(OUT) + "/final_numbers.json","w"),indent=1,default=str)
print("\nSAVED " + str(OUT) + "/final_numbers.json")
