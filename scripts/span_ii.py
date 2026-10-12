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

"""Explain the affected-span sizes level by level, and lock Table II using real-record leaves."""
import csv, glob, hashlib, json, statistics, time, random
random.seed(20261012)

def h(b): return hashlib.sha256(b).digest()
def lh(x): return h(b"\x00" + x)
def nh(l, r): return h(b"\x01" + l + r)

def levels_dup(leaves):
    cur = list(leaves); lv = [cur]
    while len(cur) > 1:
        nxt = [nh(cur[i], cur[i + 1]) if i + 1 < len(cur) else nh(cur[i], cur[i])
               for i in range(0, len(cur), 2)]
        lv.append(nxt); cur = nxt
    return lv

print("== why the affected span is what it is (highest odd level decides) ==")
span = {}
for n in (100, 1000, 10000, 100000, 1000000):
    lv = levels_dup([lh(b"x%d" % i) for i in range(n)])
    best = None
    for L, cur in enumerate(lv[:-1]):
        if len(cur) % 2 == 1:
            lo = (len(cur) - 1) * (2 ** L)
            hi = min(n - 1, len(cur) * (2 ** L) - 1)
            if best is None or L > best[0]:
                best = (L, len(cur), lo, hi)
    L, cnt, lo, hi = best
    affected = hi - lo + 1
    span[str(n)] = {"levels": len(lv), "highest_odd_level": L, "nodes_at_that_level": cnt,
                    "affected_lo": lo, "affected_hi": hi, "affected": affected,
                    "pct": round(100.0 * affected / n, 3)}
    print("  n=%-8d highest odd level L=%-2d (%d nodes) -> leaves [%d,%d] = %d = %.3f%% of n"
          % (n, L, cnt, lo, hi, affected, 100.0 * affected / n))

# direct cross-check: every leaf whose RFC-shaped proof fails against the duplication root
def promo_proof(idx, lv):
    p = []
    for L in range(len(lv) - 1):
        cur = lv[L]; s = idx ^ 1
        if s < len(cur): p.append(("R" if s > idx else "L", cur[s]))
        idx //= 2
    return p

def verify(data, p, root):
    c = lh(data)
    for side, sib in p: c = nh(c, sib) if side == "R" else nh(sib, c)
    return c == root

print("\n== direct check on the small sizes ==")
for n in (100, 1000, 10000):
    data = [("rec%d" % i).encode() for i in range(n)]
    lv = levels_dup([lh(d) for d in data]); root = lv[-1][0]
    bad = [i for i in range(n) if not verify(data[i], promo_proof(i, lv), root)]
    s = span[str(n)]
    print("  n=%-6d failures=%-5d (%s)  predicted range [%d,%d] -> %s"
          % (n, len(bad), (bad[0], bad[-1]) if bad else None, s["affected_lo"], s["affected_hi"],
             "MATCH" if bad and bad[0] == s["affected_lo"] and bad[-1] == s["affected_hi"] and len(bad) == s["affected"] else "MISMATCH"))

# ---------- Table II on real clinical record leaves ----------
D = DATA
def rd(p):
    with open(p, newline="") as f: return list(csv.DictReader(f))
from collections import defaultdict
miiv_adm = rd(f"{D}/miiv_admissions.csv"); miiv_dx = rd(f"{D}/miiv_diagnoses_icd.csv")
miiv_lab = rd(f"{D}/miiv_labevents.csv"); miiv_rx = rd(f"{D}/miiv_prescriptions.csv")
miii_adm = rd(f"{D}/miii_ADMISSIONS.csv"); miii_dx = rd(f"{D}/miii_DIAGNOSES_ICD.csv")
miii_lab = rd(f"{D}/miii_LABEVENTS.csv")

def mk_record(pid, row, dx_rows, lab_rows, rx_rows, ds):
    return json.dumps({"dataset": ds, "patient_id": pid, "admission_type": row.get("admission_type", ""),
                       "insurance": row.get("insurance", ""), "admittime": row.get("admittime", ""),
                       "dischtime": row.get("dischtime", ""),
                       "diagnosis_codes": sorted({d.get("icd_code", "") for d in dx_rows})[:12],
                       "lab_events": len(lab_rows), "prescriptions": len(rx_rows),
                       "gender": row.get("gender", "")}, sort_keys=True).encode()

dx_by = defaultdict(list); lab_by = defaultdict(list); rx_by = defaultdict(list)
for d in miiv_dx: dx_by[d.get("hadm_id")].append(d)
for d in miiv_lab: lab_by[d.get("hadm_id")].append(d)
for d in miiv_rx: rx_by[d.get("hadm_id")].append(d)
recs = [mk_record(a.get("subject_id"), a, dx_by[a.get("hadm_id")], lab_by[a.get("hadm_id")], rx_by[a.get("hadm_id")], "MIMIC-IV") for a in miiv_adm[:100]]
dx3 = defaultdict(list); lab3 = defaultdict(list)
for d in miii_dx: dx3[d.get("HADM_ID")].append(d)
for d in miii_lab: lab3[d.get("HADM_ID")].append(d)
recs += [mk_record(a.get("SUBJECT_ID"), a, dx3[a.get("HADM_ID")], lab3[a.get("HADM_ID")], [], "MIMIC-III") for a in miii_adm[:100]]
sz = sorted(len(r) for r in recs)
print("\n== real store: %d records, median %d bytes, range %d-%d ==" % (len(recs), statistics.median(sz), sz[0], sz[-1]))


def build(leaves):
    cur = list(leaves); lv = [cur]
    while len(cur) > 1:
        nxt = [nh(cur[i], cur[i + 1]) for i in range(0, len(cur) - 1, 2)]
        if len(cur) % 2: nxt.append(cur[-1])
        lv.append(nxt); cur = nxt
    return lv

def prf(lv, idx):
    p = []
    for L in range(len(lv) - 1):
        cur = lv[L]; s = idx ^ 1
        if s < len(cur): p.append(("R" if s > idx else "L", cur[s]))
        idx //= 2
    return p

def vf(data, idx, p, root):
    c = lh(data)
    for side, sib in p: c = nh(c, sib) if side == "R" else nh(sib, c)
    return c == root

T = {}
for n, passes, sample in ((100000, 5, 2400), (1000000, 5, 2400)):
    leaves = [lh(recs[i % len(recs)]) for i in range(n)]   # real record bytes, cycled
    tb = []; au = []; ge = []; ve = []; ff = 0; pl = 0
    for _ in range(passes):
        base = [lh(recs[i % len(recs)]) for i in range(n)]
        t0 = time.perf_counter(); lv = build(base); tb.append((time.perf_counter() - t0) * 1e3)
        root = lv[-1][0]; pl = len(lv) - 1
        for i in random.sample(range(n), sample):
            t0 = time.perf_counter(); p = prf(lv, i); ge.append((time.perf_counter() - t0) * 1e6)
            t0 = time.perf_counter()
            if not vf(recs[i % len(recs)], i, p, root): ff += 1
            ve.append((time.perf_counter() - t0) * 1e6)
        del base, lv
    T[str(n)] = {"n": n, "tree_ms": statistics.median(tb), "proof_len": pl,
                 "assembly_us": statistics.median(ge), "verify_us": statistics.median(ve),
                 "false_failures": ff, "sampled": sample * passes}
    print("  n=%-8d tree %.0f ms | len %d | assembly %.2f us | verify %.2f us | false failures %d/%d"
          % (n, T[str(n)]["tree_ms"], pl, T[str(n)]["assembly_us"], T[str(n)]["verify_us"], ff, sample * passes))

json.dump({"span": span, "table_ii": T, "store": {"records": len(recs), "median_bytes": statistics.median(sz),
           "min": sz[0], "max": sz[-1]}}, open(str(OUT) + "/span_ii.json", "w"), indent=2)
print("\nSAVED span_ii.json")
