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

"""Round-5 detection-delay, measured correctly.

The tick is the WAIT from the modification to the next scheduled verification sweep -- the sweep is a
fixed periodic check over the store, not a scan in search of the tamper. The sweep cost is measured and
reported separately so it cannot masquerade as the delay (an earlier version of this script scanned
20,000 records looking for the tamper, which made every interval look ~415 ms)."""
import hashlib, json, os, random, statistics, subprocess, time
random.seed(20260512)
TSA = "https://rfc3161.ai.moda"
TMP = WORK; os.makedirs(TMP, exist_ok=True)
def h(b): return hashlib.sha256(b).digest()
def lh(x): return h(b"\x00" + x)
def nh(l, r): return h(b"\x01" + l + r)
def build(lv):
    cur = list(lv); out = [cur]
    while len(cur) > 1:
        nxt = [nh(cur[i], cur[i + 1]) for i in range(0, len(cur) - 1, 2)]
        if len(cur) % 2: nxt.append(cur[-1])
        out.append(nxt); cur = nxt
    return out
def prf(L, i):
    p = []
    for k in range(len(L) - 1):
        c = L[k]; s = i ^ 1
        if s < len(c): p.append(("R" if s > i else "L", c[s]))
        i //= 2
    return p
def vf(d_, p, root_):
    c = lh(d_)
    for side, sib in p: c = nh(c, sib) if side == "R" else nh(sib, c)
    return c == root_

def anchor(root_bytes):
    open(f"{TMP}/d.bin", "wb").write(root_bytes)
    subprocess.run(["openssl", "ts", "-query", "-data", f"{TMP}/d.bin", "-sha256", "-cert",
                    "-out", f"{TMP}/q.tsq"], check=True, capture_output=True)
    t0 = time.perf_counter()
    subprocess.run(["curl", "-s", "-m", "25", "-H", "Content-Type: application/timestamp-query",
                    "--data-binary", f"@{TMP}/q.tsq", TSA, "-o", f"{TMP}/r.tsr"], capture_output=True)
    return (time.perf_counter() - t0) * 1e3

def med(x): return round(statistics.median(x), 1)
def mean(x): return round(statistics.mean(x), 1)

N = 2000                       # store size: the periodic sweep is over this many records
OUT = {"tsa": TSA, "store_records": N, "sweep_definition": "periodic verification over the store",
       "connection": "fresh process and connection per anchor request", "intervals": {}}

# measure one sweep's own cost
store0 = [("rec%d" % i).encode() for i in range(N)]
L0 = build([lh(x) for x in store0]); r0 = L0[-1][0]
sw = []
for _ in range(5):
    t0 = time.perf_counter()
    for i in range(N):
        vf(store0[i], prf(L0, i), r0)
    sw.append((time.perf_counter() - t0) * 1e3)
OUT["sweep_ms_median"] = med(sw); OUT["sweep_ms_mean"] = mean(sw)
print("one full verification sweep over %d records: median %.1f ms" % (N, OUT["sweep_ms_median"]))

for T, NT in ((0.2, 40), (1.0, 40), (5.0, 12)):
    ticks, rtts, tots, found = [], [], [], 0
    for _ in range(NT):
        store = list(store0); L = list(L0); root = r0
        target = random.randrange(N)
        frac = random.random()
        # the modification lands part-way through the current interval
        time.sleep(T * frac)
        t_tamper = time.perf_counter()
        store[target] = b"TAMPERED" + store[target]
        # wait for the next scheduled sweep; run it and see whether it catches the modification
        remaining = T * (1.0 - frac)
        if remaining > 0: time.sleep(remaining)
        t_sweep = time.perf_counter()
        caught = False
        for i in range(N):
            if not vf(store[i], prf(L, i), root):
                caught = True; break
        if not caught:
            continue
        found += 1
        tick = (t_sweep - t_tamper) * 1e3            # wait to the sweep (detection instant)
        t0 = time.perf_counter(); anchor(root); rtt = (time.perf_counter() - t0) * 1e3
        ticks.append(tick); rtts.append(rtt); tots.append(tick + rtt)
    I = {"T_s": T, "trials": NT, "detected": found,
         "tick_median": med(ticks), "tick_mean": mean(ticks), "tick_min": round(min(ticks),1), "tick_max": round(max(ticks),1),
         "rtt_median": med(rtts), "rtt_mean": mean(rtts), "rtt_min": round(min(rtts),1), "rtt_max": round(max(rtts),1),
         "total_median": med(tots), "total_mean": mean(tots), "total_min": round(min(tots),1), "total_max": round(max(tots),1)}
    I["means_sum"] = round(I["tick_mean"] + I["rtt_mean"], 1)
    I["medians_sum"] = round(I["tick_median"] + I["rtt_median"], 1)
    I["ideal_T_over_2"] = round(T * 500, 1)
    OUT["intervals"][str(T)] = I
    print("T=%-4s n=%2d det=%d | tick med %6.1f mean %6.1f (%4.1f-%5.1f) | rtt med %5.1f mean %5.1f | "
          "total med %6.1f mean %6.1f | ideal T/2 %.0f"
          % (T, NT, found, I["tick_median"], I["tick_mean"], I["tick_min"], I["tick_max"],
             I["rtt_median"], I["rtt_mean"], I["total_median"], I["total_mean"], I["ideal_T_over_2"]))
    print("      means sum %.1f vs total_mean %.1f -> additive? %s | medians sum %.1f vs total_median %.1f"
          % (I["means_sum"], I["total_mean"], abs(I["means_sum"] - I["total_mean"]) < 1.5,
             I["medians_sum"], I["total_median"]))

json.dump(OUT, open(str(OUT) + "/round5_delay2.json", "w"), indent=2)
print("\nSAVED round5_delay2.json")
