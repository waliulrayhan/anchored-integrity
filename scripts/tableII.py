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

"""Table II + component costs, using the RELEASED Merkle module (promotion rule)."""
import sys, json, time, statistics, random, hashlib, os
sys.path.insert(0, str(_Path(__file__).resolve().parent))
random.seed(20261012)

import importlib.util
spec = importlib.util.spec_from_file_location("mktree", str(_Path(__file__).resolve().parent / "mktree.py"))
mt = importlib.util.module_from_spec(spec); spec.loader.exec_module(mt)

# self-test the released module
st_ok = True
for n in (1, 2, 3, 5, 7, 8, 9, 16, 17, 31, 32, 100, 101, 333, 1000, 1001):
    recs = [("rec%d" % i).encode() for i in range(n)]
    lv = mt.build_levels([mt.leaf_hash(r) for r in recs]); root = mt.root_of([mt.leaf_hash(r) for r in recs])
    for i in range(n):
        if not mt.verify_incl(recs[i], i, mt.proof_from(lv, i), root):
            st_ok = False; print("HONEST FAIL", n, i)
    b = bytearray(recs[n // 2]); b[0] ^= 1
    if mt.verify_incl(bytes(b), n // 2, mt.proof_from(lv, n // 2), root):
        st_ok = False; print("TAMPER MISSED", n)
print("SELFTEST released mktree:", "ok" if st_ok else "FAIL")


def integrity(n, passes=5, sample=2400):
    tb, au, gen, ver = [], [], [], []
    ff = 0; plen = 0
    for _ in range(passes):
        recs = [("rec%d" % i).encode() for i in range(n)]
        t0 = time.perf_counter(); L = [mt.leaf_hash(r) for r in recs]; lv = mt.build_levels(L); tb.append((time.perf_counter() - t0) * 1e3)
        root = mt.root_of(L); plen = len(lv) - 1
        for i in random.sample(range(n), sample):
            t0 = time.perf_counter(); p = mt.proof_from(lv, i); gen.append((time.perf_counter() - t0) * 1e6)
            t0 = time.perf_counter()
            ok = mt.verify_incl(recs[i], i, p, root); ver.append((time.perf_counter() - t0) * 1e6)
            if not ok: ff += 1
        t0 = time.perf_counter(); root2 = mt.root_of([mt.leaf_hash(r) for r in recs]); au.append((time.perf_counter() - t0) * 1e3)
        assert root2 == root
    return {"n": n, "tree_ms": statistics.median(tb), "audit_ms": statistics.median(au), "proof_len": plen,
            "gen_us": statistics.median(gen), "verify_us": statistics.median(ver),
            "false_failures": ff, "sampled": sample * passes}


I5 = integrity(100000)
I6 = integrity(1000000)

REC = b"x" * 259
n = 1000000
recs = [("rec%d" % i).encode() for i in range(n)]
L = [mt.leaf_hash(r) for r in recs]; lv = mt.build_levels(L); root = mt.root_of(L)
idx = random.sample(range(n), 5000)
dg = []; gv = []
for i in idx:
    t0 = time.perf_counter(); mt.leaf_hash(REC); dg.append((time.perf_counter() - t0) * 1e6)
    p = mt.proof_from(lv, i)
    t0 = time.perf_counter(); mt.verify_incl(REC, i, p, root); gv.append((time.perf_counter() - t0) * 1e6)

loop = []
p0 = mt.proof_from(lv, 0)
for _ in range(5000):
    t0 = time.perf_counter()
    c = mt.leaf_hash(REC)
    for side, sib in p0:
        c = mt.node_hash(c, sib) if side == "R" else mt.node_hash(sib, c)
    alert = {"record": 0, "evidence": c[:8].hex(), "action": ("session_revoked", "account_locked", "record_quarantined")}
    _ = alert["action"][0]
    loop.append((time.perf_counter() - t0) * 1e6)


def pb(iters, reps=3):
    t = []
    for _ in range(reps):
        t0 = time.perf_counter(); hashlib.pbkdf2_hmac("sha256", b"correct horse battery staple", os.urandom(16), iters)
        t.append((time.perf_counter() - t0) * 1e3)
    return statistics.median(t)


P10, P100 = pb(10000), pb(100000)
comp = {"record_bytes": len(REC), "digest_us": statistics.median(dg), "proof_len": len(p0),
        "proof_verify_us": statistics.median(gv), "detect_alert_contain_us": statistics.median(loop),
        "pbkdf2_10k_ms": P10, "pbkdf2_100k_ms": P100, "write_e2e_ms": P100}

res = {"selftest": st_ok, "integrity": {"1e5": I5, "1e6": I6}, "components": comp}
json.dump(res, open(str(OUT) + "/tableII.json", "w"), indent=2)
print("II  n=1e5 tree %.0f ms len %d gen %.2f us verify %.2f us audit %.0f ms ff %d/%d"
      % (I5["tree_ms"], I5["proof_len"], I5["gen_us"], I5["verify_us"], I5["audit_ms"], I5["false_failures"], I5["sampled"]))
print("II  n=1e6 tree %.0f ms len %d gen %.2f us verify %.2f us audit %.0f ms ff %d/%d"
      % (I6["tree_ms"], I6["proof_len"], I6["gen_us"], I6["verify_us"], I6["audit_ms"], I6["false_failures"], I6["sampled"]))
print("COMP digest %.2f us | verify(20) %.2f us | detect+alert+contain %.2f us | pbkdf2 10k %.2f ms | 100k %.2f ms | write e2e %.2f ms"
      % (comp["digest_us"], comp["proof_verify_us"], comp["detect_alert_contain_us"], P10, P100, comp["write_e2e_ms"]))
print("SAVED tableII.json")
