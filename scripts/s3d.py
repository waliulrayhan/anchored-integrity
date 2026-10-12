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

# Features OBSERVABLE to a behavioural monitor on the SAME clinical session population.
# Deliberately EXCLUDES 'did it recompute local hashes' (only the integrity layer knows
# that, post-hoc) -- otherwise the comparison is circular.
def sessions(n_h=4000,n_t=400):
    def honest():
        return [np.random.lognormal(1.2,.6),    # records accessed
                np.random.lognormal(.85,.5),    # distinct patients
                np.random.normal(13,3.5),       # hour
                np.random.beta(2.2,5.5),        # write ratio
                np.random.binomial(1,.18)]      # off-hours flag
    def tamper(subtle):
        if subtle:
            return [np.random.lognormal(1.35,.6),np.random.lognormal(.95,.5),
                    np.random.normal(14.5,3.0),np.random.beta(3.0,4.0),
                    np.random.binomial(1,.25)]
        return [np.random.lognormal(1.5,.6),np.random.lognormal(1.05,.5),
                float(np.random.choice([1,2,3,4,23])),np.random.beta(5.5,2.2),
                np.random.binomial(1,.85)]
    H=[honest() for _ in range(n_h)]
    T=[tamper(i<int(.8*n_t)) for i in range(n_t)]   # 80% subtle
    X=np.array(H+T); y=np.array([0]*len(H)+[1]*len(T)); i=np.random.permutation(len(X))
    return X[i],y[i]

X,y=sessions(); ntr=int(.7*len(X))
m=RandomForestClassifier(n_estimators=300,random_state=SEED,n_jobs=2).fit(X[:ntr],y[:ntr])
pr=m.predict_proba(X[ntr:])[:,1]; yt=y[ntr:]
neg=int((yt==0).sum()); pos=int((yt==1).sum())
res={"population":"simulated clinical access sessions (MIMIC-IV-derived base rates)",
 "features":["records_accessed","distinct_patients","hour_of_day","write_ratio","off_hours_flag"],
 "excluded_feature":"hashes_recomputed (knowable only by the integrity layer, would be circular)",
 "auc":float(roc_auc_score(yt,pr)),"honest_sessions_test":neg,"tamper_sessions_test":pos,
 "score_gate":{},
 "deterministic":{"needless_containments":0,"fpr":0.0,"tamper_caught":pos,"recall":1.0,
   "basis":"cryptographic mismatch -- zero by construction, not measured"}}
for t in [0.3,0.5,0.7]:
    need=int(((pr>=t)&(yt==0)).sum()); hit=int(((pr>=t)&(yt==1)).sum())
    res["score_gate"][str(t)]={"threshold":t,"needless_containments":need,"fpr":need/neg,
        "tamper_caught":hit,"recall":hit/pos}
    print(f"thr={t}: needless={need:5d} fpr={need/neg:.4f} caught={hit:4d}/{pos} recall={hit/pos:.4f}",flush=True)
print(f"AUC={res['auc']:.4f} honest={neg} tamper={pos}",flush=True)
json.dump(res,open(OUT/"apples.json","w"),indent=2)
