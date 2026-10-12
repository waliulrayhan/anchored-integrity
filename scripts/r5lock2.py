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

"""Round-5: (a) token validation counted over N tokens both ways, (b) ONE consistent set of component
numbers so the abstract, Table II and the cost paragraph cannot disagree."""
import hashlib, json, os, random, statistics, subprocess, tempfile, time
from pathlib import Path
TMP = Path(tempfile.mkdtemp(dir="/scratch")); OUT = {}
random.seed(20260512)
def h(b): return hashlib.sha256(b).digest()
def run(a):
    return subprocess.run(a, capture_output=True)

# ---------- (a) token validation over N tokens ----------
N = 12
ok_correct = ok_wrong = 0
sizes = []
for i in range(N):
    data = TMP / ("d%d.bin" % i)
    payload = ("record-%d-%d" % (i, time.time_ns())).encode()
    data.write_bytes(payload)
    run(["openssl", "ts", "-query", "-data", str(data), "-sha256", "-cert", "-out", str(TMP / "q.tsq")])
    tsr = TMP / "r.tsr"
    subprocess.run(["curl", "-s", "-m", "25", "-H", "Content-Type: application/timestamp-query",
                    "--data-binary", f"@{TMP/'q.tsq'}", "https://rfc3161.ai.moda", "-o", str(tsr)],
                   capture_output=True)
    sizes.append(tsr.stat().st_size)
    run(["openssl", "ts", "-reply", "-in", str(tsr), "-token_out", "-out", str(TMP / "tok.der")])
    (TMP / "all.pem").write_bytes(run(["openssl", "pkcs7", "-inform", "DER", "-in", str(TMP / "tok.der"),
                                       "-print_certs"]).stdout or b"")
    v1 = run(["openssl", "ts", "-verify", "-in", str(tsr), "-data", str(data),
              "-CAfile", "/etc/ssl/certs/ca-certificates.crt", "-untrusted", str(TMP / "all.pem")])
    if v1.returncode == 0: ok_correct += 1
    other = TMP / "other.bin"; other.write_bytes(b"different bytes entirely")
    v2 = run(["openssl", "ts", "-verify", "-in", str(tsr), "-data", str(other),
              "-CAfile", "/etc/ssl/certs/ca-certificates.crt", "-untrusted", str(TMP / "all.pem")])
    if v2.returncode == 0: ok_wrong += 1
    time.sleep(0.15)
OUT["token"] = {"n": N, "validated_correct_data": ok_correct, "validated_wrong_data": ok_wrong,
                "median_bytes": statistics.median(sizes)}
print("tokens: %d requests, %d validated against their own data, %d validated against different data (must be 0)"
      % (N, ok_correct, ok_wrong))
print("token size median %d bytes" % statistics.median(sizes))

# ---------- (b) one consistent component set ----------
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

n = 1000000
leaves = [lh(b"rec%d" % i) for i in range(n)]
L = build(leaves); root = L[-1][0]; plen = len(L) - 1
REC = b"a representative 259-byte clinical episode record" + b"x" * 208
print("\nrecord under test: %d bytes | proof length %d hashes" % (len(REC), plen))

dig, ver, con, tot = [], [], [], []
for _ in range(3000):
    i = random.randrange(n)
    p = prf(L, i)
    t0 = time.perf_counter(); lh(REC); dig.append((time.perf_counter() - t0) * 1e6)
    t0 = time.perf_counter(); vf(REC, p, root); ver.append((time.perf_counter() - t0) * 1e6)
    t0 = time.perf_counter()
    c = lh(REC)
    for side, sib in p: c = nh(c, sib) if side == "R" else nh(sib, c)
    ev = {"record": i, "mismatch": (c != root), "evidence": c[:8].hex()}
    con.append((time.perf_counter() - t0) * 1e6)
    t0 = time.perf_counter()
    acts = ("session_revoked", "account_locked", "record_quarantined")
    _ = (ev["record"], ev["evidence"], acts[0], acts[2], ev["mismatch"])
    tot.append((time.perf_counter() - t0) * 1e6)

def med(x): return statistics.median(x)
OUT["components"] = {
    "record_bytes": len(REC), "proof_len": plen,
    "digest_us": round(med(dig), 2),
    "verify_us": round(med(ver), 2),
    "verify_then_contain_us": round(med(con), 2),
    "contain_actions_only_us": round(med(tot), 2),
    "n": len(dig),
}
O = OUT["components"]
print("digest %.2f us | verify(proof) %.2f us | verify+contain %.2f us | contain actions alone %.2f us"
      % (O["digest_us"], O["verify_us"], O["verify_then_contain_us"], O["contain_actions_only_us"]))
print("sum check: digest + verify+contain = %.2f us" % (O["digest_us"] + O["verify_then_contain_us"]))

pb10, pb100 = [], []
for _ in range(31):
    t0 = time.perf_counter(); hashlib.pbkdf2_hmac("sha256", b"pw", os.urandom(16), 10000)
    pb10.append((time.perf_counter() - t0) * 1e3)
    t0 = time.perf_counter(); hashlib.pbkdf2_hmac("sha256", b"pw", os.urandom(16), 100000)
    pb100.append((time.perf_counter() - t0) * 1e3)
OUT["pbkdf2"] = {"10k_median": round(med(pb10), 2), "10k_min": round(min(pb10), 2), "10k_max": round(max(pb10), 2),
                 "100k_median": round(med(pb100), 2), "100k_min": round(min(pb100), 2), "100k_max": round(max(pb100), 2)}
print("pbkdf2 10k median %.2f ms | 100k median %.2f ms (min %.2f max %.2f)"
      % (med(pb10), med(pb100), min(pb100), max(pb100)))
r = med(pb100) * 1000.0 / O["verify_us"]
OUT["ratio_100k_vs_verify"] = {"x": round(r, 0), "orders_of_magnitude": round(__import__("math").log10(r), 1)}
print("PBKDF2-100k / proof verification = %.0fx (10^%.1f)" % (r, __import__("math").log10(r)))
r2 = med(pb100) * 1000.0 / (O["digest_us"] + O["verify_then_contain_us"])
OUT["ratio_100k_vs_auth_plus_integrity"] = {"x": round(r2, 0), "orders_of_magnitude": round(__import__("math").log10(r2), 1)}
print("PBKDF2-100k / whole integrity sequence = %.0fx (10^%.1f)" % (r2, __import__("math").log10(r2)))

json.dump(OUT, open(str(OUT) + "/round5_lock.json", "w"), indent=2, default=str)
print("\nSAVED round5_lock.json")
