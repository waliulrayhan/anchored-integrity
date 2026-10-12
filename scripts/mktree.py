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

"""Corrected Merkle log (RFC 6962 semantics).

Earlier version DUPLICATED the final node when a level had an odd count. That
produces a tree whose proofs do not reconstruct the root for leaves on the right
spine (~1.7% at n=1e6). A correct RFC 6962 tree PROMOTES the unpaired node.
"""
import hashlib
def h(b): return hashlib.sha256(b).digest()
def leaf_hash(rb): return h(b"\x00"+rb)
def node_hash(l,r): return h(b"\x01"+l+r)

def build_levels(L):
    if not L: return [[]]
    lv=[list(L)]; cur=list(L)
    while len(cur)>1:
        nxt=[node_hash(cur[i],cur[i+1]) for i in range(0,len(cur)-1,2)]
        if len(cur)%2: nxt.append(cur[-1])          # PROMOTE, do not duplicate
        lv.append(nxt); cur=nxt
    return lv

def root_of(L): return build_levels(L)[-1][0] if L else h(b"")

def proof_from(lv,i):
    p=[]
    for L in range(len(lv)-1):
        c=lv[L]; s=i^1
        if s<len(c): p.append(("R" if s>i else "L", c[s]))
        i//=2
    return p

def verify_incl(rb,i,p,root):
    c=leaf_hash(rb)
    for side,sib in p: c = node_hash(c,sib) if side=="R" else node_hash(sib,c)
    return c==root
