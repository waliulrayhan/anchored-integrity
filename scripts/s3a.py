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

import json,time,random
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score,precision_score,recall_score,f1_score,roc_auc_score
DATA=Path(NSLKDD); OUT=Path(OUT); OUT.mkdir(parents=True,exist_ok=True)
SEED=20261011; random.seed(SEED); np.random.seed(SEED)
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
Xte,yte=load(DATA/"KDDTest+.txt")
clf=RandomForestClassifier(n_estimators=100,random_state=SEED,n_jobs=2).fit(Xtr,ytr)
proba=clf.predict_proba(Xte)[:,1]; pred=(proba>=.5).astype(int)
base={"n_test":int(len(yte)),"attacks":int(yte.sum()),"acc":float(accuracy_score(yte,pred)),
 "prec":float(precision_score(yte,pred)),"rec":float(recall_score(yte,pred)),
 "f1":float(f1_score(yte,pred)),"auc":float(roc_auc_score(yte,proba))}
print("BASE",{k:round(v,4) if isinstance(v,float) else v for k,v in base.items()},flush=True)
# per-row inference latency (single-row predict, as in a live loop)
lat=[]
for i in range(300):
    t0=time.perf_counter(); clf.predict_proba(Xte[i].reshape(1,-1)); lat.append((time.perf_counter()-t0)*1000)
inf=float(np.median(lat))
print(f"INFER median={inf:.4f}ms/row  p95={np.percentile(lat,95):.4f}",flush=True)
json.dump({"base":base,"infer_ms_median":inf,"infer_ms_p95":float(np.percentile(lat,95)),"seed":SEED},
          open(OUT/"s3a.json","w"),indent=2)
np.save(OUT/"proba.npy",proba); np.save(OUT/"yte.npy",yte)
print("saved",flush=True)
