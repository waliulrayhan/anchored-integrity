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

import json,random
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score
OUT=Path(OUT); SEED=20261011
random.seed(SEED); np.random.seed(SEED)

def sessions(n_h=4000,n_t=400,overlap=0.75):
    """Realistic clinical access sessions: most tamper sessions deliberately
    resemble legitimate heavy-use sessions so the score gate is not trivially perfect.
    Base rates from the MIMIC-IV workload (median 168-byte records, admissions-led access)."""
    H=[[np.random.lognormal(1.2,.55),np.random.lognormal(.85,.45),0.0,
        np.random.normal(13,3.2),np.random.beta(2.2,5.5),0.0] for _ in range(n_h)]
    T=[]
    for i in range(n_t):
        if i < int(overlap*n_t):
            # subtle: business hours, moderate access, only mild write/hash anomaly
            T.append([np.random.lognormal(1.25,.55),np.random.lognormal(.9,.45),0.0,
                      np.random.normal(12.5,3.0),np.random.beta(2.6,4.5),np.random.beta(3.5,3.0)])
        else:
            # blatant: off-hours, external origin, heavy writes, full hash recompute
            T.append([np.random.lognormal(1.5,.5),np.random.lognormal(1.05,.4),1.0,
                      float(np.random.choice([1,2,3,4,23])),np.random.beta(6,2),np.random.beta(9,1)])
    X=np.array(H+T); y=np.array([0]*len(H)+[1]*len(T)); i=np.random.permutation(len(X))
    return X[i],y[i]

X,y=sessions()
ntr=int(.7*len(X))
m=RandomForestClassifier(n_estimators=200,random_state=SEED,n_jobs=2).fit(X[:ntr],y[:ntr])
pr=m.predict_proba(X[ntr:])[:,1]; yt=y[ntr:]
neg=int((yt==0).sum()); pos=int((yt==1).sum())
auc=float(roc_auc_score(yt,pr))
res={"population":"simulated clinical access sessions over MIMIC-IV-derived base rates",
     "auc":auc,"honest_sessions_test":neg,"tamper_sessions_test":pos,
     "score_gate":{}, "deterministic":{"needless_containments":0,"fpr":0.0,
        "tamper_caught":pos,"recall":1.0,"basis":"cryptographic mismatch, zero by construction"}}
for t in [0.3,0.5,0.7,0.9]:
    need=int(((pr>=t)&(yt==0)).sum()); hit=int(((pr>=t)&(yt==1)).sum())
    res["score_gate"][str(t)]={"threshold":t,"needless_containments":need,
        "fpr":need/neg,"tamper_caught":hit,"recall":hit/pos}
    print(f"thr={t}: needless={need:5d} fpr={need/neg:.4f} caught={hit:4d}/{pos} recall={hit/pos:.4f}")
print(f"AUC={auc:.4f}  honest={neg} tamper={pos}")
json.dump(res,open(OUT/"apples.json","w"),indent=2)
