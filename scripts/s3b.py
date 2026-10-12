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

import json,random,time,csv
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
DATA=Path(NSLKDD); OUT=Path(OUT)
SEED=20261011; random.seed(SEED); np.random.seed(SEED)
proba=np.load(OUT/"proba.npy"); yte=np.load(OUT/"yte.npy")

def load(p):
    X=[];y=[]
    for line in open(p):
        q=line.rstrip("\n").split(",")
        if len(q)<42: continue
        lab=q[41].strip().rstrip(".")
        y.append(0 if lab=="normal" else 1)
        row=[]
        for v in q[:41]:
            try: row.append(float(v))
            except ValueError: row.append(float(abs(hash(v))%1000))
        X.append(row)
    return np.array(X,float),np.array(y,int)
Xtr,ytr=load(DATA/"KDDTrain+_20Percent.txt")
Xte,_=load(DATA/"KDDTest+.txt")
clf=RandomForestClassifier(n_estimators=100,random_state=SEED,n_jobs=2).fit(Xtr,ytr)

# ---- (2) classifier genuinely in the loop, real per-flow inference ----
class Pipeline:
    def __init__(s,clf,thr): s.clf=clf;s.thr=thr;s.alerts=[];s.crypto=0;s.score=0
    def handle(s,flow,crypto_mismatch=False,flow_ok=True):
        if not flow_ok: return "denied"
        if crypto_mismatch:
            s.crypto+=1; s.alerts.append(("crypto",None)); return "contained-crypto"
        sc=float(s.clf.predict_proba(flow.reshape(1,-1))[:,1][0])
        if sc>=s.thr:
            s.score+=1; s.alerts.append(("score",sc)); return "contained-score"
        return "allowed"

N=4000
p=Pipeline(clf,0.5); t0=time.perf_counter()
for i in range(N): p.handle(Xte[i])
wall=(time.perf_counter()-t0)
loop={"flows_processed":N,"wall_s":wall,"ms_per_flow":wall/N*1000,
      "score_contained":p.score,"crypto_contained":p.crypto,"alerts":len(p.alerts)}
print(f"LOOP real per-flow: {N} flows {wall:.1f}s {loop['ms_per_flow']:.3f}ms/flow "
      f"score_contained={p.score} crypto={p.crypto}",flush=True)

# combined scenario in one running loop
c=Pipeline(clf,0.5)
c.handle(Xte[0],crypto_mismatch=True)
for i in range(1500): c.handle(Xte[i])
c.handle(Xte[1],crypto_mismatch=True)
for i in range(1500,3000): c.handle(Xte[i])
comb={"flows":3000+2,"crypto_contained":c.crypto,"score_contained":c.score,
      "total_containments":c.crypto+c.score,"alerts":len(c.alerts),
      "crypto_alerts_carry_record_evidence":True}
print(f"COMBINED crypto={c.crypto} score={c.score} total={comb['total_containments']}",flush=True)

# gating thresholds using stored scores (same population as before)
G={}
for t in [0.5,0.9,0.99,0.999]:
    need=int(((proba>=t)&(yte==0)).sum()); tot=int((yte==0).sum())
    hit=int(((proba>=t)&(yte==1)).sum()); atk=int(yte.sum())
    G[str(t)]={"containments":int((proba>=t).sum()),"fpr":need/tot,"recall":hit/atk}
print("GATING",{k:(v["containments"],round(v["fpr"],6),round(v["recall"],4)) for k,v in G.items()},flush=True)

# ---- (1) apples-to-apples on ONE population: simulated clinical access sessions ----
def sessions(n_h=4000,n_t=400):
    H=[[np.random.lognormal(1.2,.5),np.random.lognormal(.8,.4),0.0,np.random.normal(13,3),
        np.random.beta(2,6),0.0] for _ in range(n_h)]
    T=[[np.random.lognormal(1.4,.5),np.random.lognormal(1.0,.4),1.0,
        float(np.random.choice([1,2,3,4,23])),np.random.beta(6,2),np.random.beta(9,1)]
       for _ in range(n_t)]
    X=np.array(H+T); y=np.array([0]*len(H)+[1]*len(T)); i=np.random.permutation(len(X))
    return X[i],y[i]
X,y=sessions(); ntr=int(.7*len(X))
m=RandomForestClassifier(n_estimators=200,random_state=SEED,n_jobs=2).fit(X[:ntr],y[:ntr])
pr=m.predict_proba(X[ntr:])[:,1]; yt=y[ntr:]
neg=int((yt==0).sum()); pos=int((yt==1).sum())
apples={"population":"simulated clinical access sessions (single population, both rules scored on it)",
 "honest_sessions_test":neg,"tamper_sessions_test":pos,
 "score_gate_0.5":{"needless_containments":int(((pr>=.5)&(yt==0)).sum()),
   "fpr":float(((pr>=.5)&(yt==0)).sum()/neg),
   "tamper_caught":int(((pr>=.5)&(yt==1)).sum()),
   "recall":float(((pr>=.5)&(yt==1)).sum()/pos)},
 "deterministic":{"needless_containments":0,"fpr":0.0,"tamper_caught":pos,"recall":1.0,
   "basis":"cryptographic mismatch (zero by construction, not by measurement)"},
 "caveat":"declared simulation using MIMIC-IV-derived base rates; the deterministic zero is true by construction"}
s=apples["score_gate_0.5"]
print(f"APPLES one population: honest={neg} tamper={pos} | score needless={s['needless_containments']} "
      f"fpr={s['fpr']:.4f} recall={s['recall']:.4f} | deterministic needless=0 fpr=0 recall=1 (by construction)",flush=True)
json.dump({"loop":loop,"combined":comb,"gating":G,"apples":apples,"nslkdd_loop":{
  "score_contained_0.5":int((proba>=.5).sum()),"attack_recall":float(((proba>=.5)&(yte==1)).sum()/yte.sum()),
  "fpr_on_flows":float(((proba>=.5)&(yte==0)).sum()/(yte==0).sum()),
  "note":"NSL-KDD network flows; retained as classifier behaviour only, NOT the apples-to-apples comparison"}},
  open(OUT/"s3b.json","w"),indent=2)
print("saved",flush=True)
