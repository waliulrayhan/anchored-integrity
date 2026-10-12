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

import hashlib, json, random, time, statistics, os
random.seed(20261012)

def h(b): return hashlib.sha256(b).digest()
def lh(x): return h(b"\x00" + x)
def nh(l, r): return h(b"\x01" + l + r)

def dup_levels(leaves):
    """the buggy tree: an unpaired node is paired with itself"""
    lv = [list(leaves)]
    while len(lv[-1]) > 1:
        cur = lv[-1]
        lv.append([nh(cur[i], cur[i+1]) if i+1 < len(cur) else nh(cur[i], cur[i])
                   for i in range(0, len(cur), 2)])
    return lv

def promo_proof(idx, lv):
    """the verifier's expectation: RFC 6962 promotion, no sibling for an unpaired node"""
    p = []
    for L in range(len(lv) - 1):
        cur = lv[L]; s = idx ^ 1
        if s < len(cur):
            p.append(("R" if s > idx else "L", cur[s]))
        idx //= 2
    return p

def verify(data, p, root):
    c = lh(data)
    for side, sib in p:
        c = nh(c, sib) if side == "R" else nh(sib, c)
    return c == root

def crosses(idx, lv):
    for L in range(len(lv) - 1):
        cur = lv[L]
        if idx % 2 == 0 and idx + 1 >= len(cur):
            return True
        idx //= 2
    return False

print("== buggy tree (duplication) + RFC-6962 proof rule ==")
res = {}
for n in (100, 1000, 10000, 100000, 1000000):
    data = [("rec%d" % i).encode() for i in range(n)]
    lv = dup_levels([lh(d) for d in data]); root = lv[-1][0]
    if n <= 10000:
        test = list(range(n))
    else:
        # the affected set is a contiguous final range; test it fully plus a random sample
        test = sorted(set(range(n - 20000, n)) | set(random.sample(range(n), 3000)))
    fail = [i for i in test if not verify(data[i], promo_proof(i, lv), root)]
    cr = [i for i in test if crosses(i, lv)]
    ff = [i for i in fail if i < (min(cr) if cr else n)]
    res[str(n)] = {"tested": len(test), "failures": len(fail), "crosses": len(cr),
                   "failures_equal_crosses": fail == cr,
                   "failures_outside_crossing_set": len(ff),
                   "first": fail[0] if fail else None, "last": fail[-1] if fail else None}
    print("  n=%-8d tested=%-6d failures=%-6d paths-crossing=%-6d same-set=%s  outside=%d  range=[%s,%s]"
          % (n, len(test), len(fail), len(cr), fail == cr, len(ff), res[str(n)]["first"], res[str(n)]["last"]))

print("\n== affected-set sizes (union of unpaired-node leaf spans) ==")
for n in (100, 1000, 10000, 100000, 1000000):
    data = [("rec%d" % i).encode() for i in range(n)]
    lv = dup_levels([lh(d) for d in data])
    aff = set(crosses(i, lv) for i in range(n))
    # count leaves whose path crosses an unpaired node, computed per level
    tot = 0
    for L in range(len(lv) - 1):
        cur = lv[L]
        if len(cur) % 2 == 1:
            i = len(cur) - 1
            tot += min(n, (i + 1) * (2 ** L)) - i * (2 ** L)
    print("  n=%-8d affected=%-7d = %7.3f%%" % (n, tot, 100.0 * tot / n))
print("  powers of two:", {n: sum(1 for L in range(len(dup_levels([lh(b'x') for _ in range(n)])) - 1)
                                if len(dup_levels([lh(b'x') for _ in range(n)])[L]) % 2 == 1)
                           for n in (64, 256, 1024, 65536)})

print("\n== stable PBKDF2 (10 reps) ==")
for it in (10000, 100000):
    t = []
    for _ in range(10):
        t0 = time.perf_counter(); hashlib.pbkdf2_hmac("sha256", b"correct horse battery staple", os.urandom(16), it)
        t.append((time.perf_counter() - t0) * 1e3)
    print("  %6d iterations: median %.2f ms  min %.2f  max %.2f" % (it, statistics.median(t), min(t), max(t)))
    res["pbkdf2_%d" % it] = {"median": statistics.median(t), "min": min(t), "max": max(t)}

json.dump(res, open(str(OUT) + "/merkle_fail.json", "w"), indent=2)
print("\nSAVED merkle_fail.json")
