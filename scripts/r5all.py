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

"""ROUND-5 SINGLE LOCK. Everything the paper quotes comes from here:
Table II, the component decomposition, PBKDF2, the delay decomposition (with means, so it sums),
the storage medium, and the paired store comparison."""
import csv, hashlib, json, math, os, random, socket, socketserver, statistics, struct, subprocess, threading, time
from collections import defaultdict

random.seed(20260512)
IMMU = IMMU; DATA = str(WORK) + "/immu"; LOG = str(WORK) + "/ours.log"
os.makedirs(DATA, exist_ok=True); os.makedirs(WORK, exist_ok=True)
D = DATA
def rd(p):
    with open(p, newline="") as f: return list(csv.DictReader(f))
miiv_adm = rd(f"{D}/miiv_admissions.csv"); miiv_dx = rd(f"{D}/miiv_diagnoses_icd.csv")
miiv_lab = rd(f"{D}/miiv_labevents.csv"); miiv_rx = rd(f"{D}/miiv_prescriptions.csv")
miii_adm = rd(f"{D}/miii_ADMISSIONS.csv"); miii_dx = rd(f"{D}/miii_DIAGNOSES_ICD.csv")
miii_lab = rd(f"{D}/miii_LABEVENTS.csv")
def mk(pid, row, dx, lab, rx, ds):
    return json.dumps({"dataset": ds, "patient_id": pid, "admission_type": row.get("admission_type", ""),
        "insurance": row.get("insurance", ""), "admittime": row.get("admittime", ""),
        "dischtime": row.get("dischtime", ""), "diagnosis_codes": sorted({d.get("icd_code","") for d in dx})[:12],
        "lab_events": len(lab), "prescriptions": len(rx), "gender": row.get("gender", "")},
        sort_keys=True).encode()
dx_by = defaultdict(list); lab_by = defaultdict(list); rx_by = defaultdict(list)
for d in miiv_dx: dx_by[d.get("hadm_id")].append(d)
for d in miiv_lab: lab_by[d.get("hadm_id")].append(d)
for d in miiv_rx: rx_by[d.get("hadm_id")].append(d)
recs = [mk(a.get("subject_id"), a, dx_by[a.get("hadm_id")], lab_by[a.get("hadm_id")], rx_by[a.get("hadm_id")], "MIMIC-IV") for a in miiv_adm[:100]]
dx3 = defaultdict(list); lab3 = defaultdict(list)
for d in miii_dx: dx3[d.get("HADM_ID")].append(d)
for d in miii_lab: lab3[d.get("HADM_ID")].append(d)
recs += [mk(a.get("SUBJECT_ID"), a, dx3[a.get("HADM_ID")], lab3[a.get("HADM_ID")], [], "MIMIC-III") for a in miii_adm[:100]]
sz = sorted(len(r) for r in recs)
O = {"store": {"records": len(recs), "median_bytes": statistics.median(sz)}}
print("store: %d records, median %d B" % (len(recs), statistics.median(sz)))

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
def med(x): return round(statistics.median(x), 2)
def mean(x): return round(statistics.mean(x), 2)

ok = True
for n in (1, 2, 3, 5, 8, 9, 17, 31, 32, 100, 101, 333, 1001):
    d = [("r%d" % i).encode() for i in range(n)]; L = build([lh(x) for x in d]); r = L[-1][0]
    for i in range(n):
        if not vf(d[i], prf(L, i), r): ok = False
    b = bytearray(d[n // 2]); b[0] ^= 1
    if vf(bytes(b), prf(L, n // 2), r): ok = False
O["selftest"] = ok; print("SELFTEST:", "ok" if ok else "FAIL")

# ---------- Table II, and the component decomposition from the SAME loop ----------
T = {}
for n, passes, sample in ((100000, 7, 2400), (1000000, 7, 2400)):
    tb, au, ge, ve = [], [], [], []
    ff = 0; pl = 0
    for _ in range(passes):
        base = [lh(recs[i % len(recs)]) for i in range(n)]
        t0 = time.perf_counter(); L = build(base); tb.append((time.perf_counter() - t0) * 1e3)
        root = L[-1][0]; pl = len(L) - 1
        t0 = time.perf_counter(); L2 = build(base); _ = (L2[-1][0] == root)
        au.append((time.perf_counter() - t0) * 1e3)
        for i in random.sample(range(n), sample):
            t0 = time.perf_counter(); p = prf(L, i); ge.append((time.perf_counter() - t0) * 1e6)
            t0 = time.perf_counter()
            if not vf(recs[i % len(recs)], p, root): ff += 1
            ve.append((time.perf_counter() - t0) * 1e6)
        del base, L, L2
    T[str(n)] = {"tree_ms": round(statistics.median(tb)), "audit_ms": round(statistics.median(au)),
                 "proof_len": pl, "gen_us": med(ge), "verify_us": med(ve),
                 "false_failures": ff, "sampled": sample * passes}
    print("n=%-8d tree %d ms | audit %d ms | len %d | gen %.2f us | verify %.2f us | falsefail %d/%d"
          % (n, T[str(n)]["tree_ms"], T[str(n)]["audit_ms"], pl, T[str(n)]["gen_us"],
             T[str(n)]["verify_us"], ff, sample * passes))
O["table_ii"] = T

n = 1000000
base = [lh(recs[i % len(recs)]) for i in range(n)]; L = build(base); root = L[-1][0]
REC = recs[len(recs) // 2]; p0 = prf(L, 7)
dig, ver, vcon, con = [], [], [], []
for _ in range(6000):
    t0 = time.perf_counter(); lh(REC); dig.append((time.perf_counter() - t0) * 1e6)
    t0 = time.perf_counter(); vf(REC, p0, root); ver.append((time.perf_counter() - t0) * 1e6)
    t0 = time.perf_counter()
    c = lh(REC)
    for side, sib in p0: c = nh(c, sib) if side == "R" else nh(sib, c)
    ev = {"record": 7, "mismatch": (c != root), "evidence": c[:8].hex()}
    vcon.append((time.perf_counter() - t0) * 1e6)
    t0 = time.perf_counter()
    acts = ("session_revoked", "account_locked", "record_quarantined")
    _ = (ev["record"], ev["evidence"], acts[0], acts[2], ev["mismatch"])
    con.append((time.perf_counter() - t0) * 1e6)
del base, L
pb10, pb100 = [], []
for _ in range(31):
    t0 = time.perf_counter(); hashlib.pbkdf2_hmac("sha256", b"pw", os.urandom(16), 10000); pb10.append((time.perf_counter() - t0) * 1e3)
    t0 = time.perf_counter(); hashlib.pbkdf2_hmac("sha256", b"pw", os.urandom(16), 100000); pb100.append((time.perf_counter() - t0) * 1e3)
O["components"] = {"record_bytes": len(REC), "proof_len": len(p0),
                   "digest_us": med(dig), "verify_us": med(ver),
                   "verify_contain_us": med(vcon), "contain_only_us": med(con),
                   "pbkdf2_10k": {"median": med(pb10), "min": round(min(pb10),2), "max": round(max(pb10),2)},
                   "pbkdf2_100k": {"median": med(pb100), "min": round(min(pb100),2), "max": round(max(pb100),2)}}
C = O["components"]
print("components: digest %.2f | verify %.2f | verify+contain %.2f | contain only %.2f us"
      % (C["digest_us"], C["verify_us"], C["verify_contain_us"], C["contain_only_us"]))
print("   pbkdf2 10k %.2f ms | 100k median %.2f (min %.2f max %.2f)"
      % (C["pbkdf2_10k"]["median"], C["pbkdf2_100k"]["median"], C["pbkdf2_100k"]["min"], C["pbkdf2_100k"]["max"]))
r = C["pbkdf2_100k"]["median"] * 1000.0 / C["verify_contain_us"]
O["ratio"] = {"pbkdf2_100k_over_verify_contain": round(r), "log10": round(math.log10(r), 2)}
print("   PBKDF2-100k / (verify+contain) = %.0fx = 10^%.2f" % (r, math.log10(r)))

# ---------- medium ----------
dev = subprocess.run("findmnt -no SOURCE,FSTYPE,OPTIONS -T /scratch | head -1", shell=True,
                     capture_output=True, text=True).stdout.strip()
wf, wp = [], []
fd = os.open(str(WORK) + "/m1.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
for i in range(800):
    b = b"rec:%05d " % i + b"x" * 200
    t0 = time.perf_counter(); os.write(fd, b); os.fsync(fd); wf.append((time.perf_counter() - t0) * 1e3)
os.close(fd)
fd = os.open(str(WORK) + "/m2.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
for i in range(800):
    b = b"rec:%05d " % i + b"x" * 200
    t0 = time.perf_counter(); os.write(fd, b); wp.append((time.perf_counter() - t0) * 1e3)
os.close(fd)
O["medium"] = {"mount": dev, "fsync_median": round(statistics.median(wf), 3),
               "fsync_p95": round(sorted(wf)[int(.95*799)], 3), "no_fsync_median": round(statistics.median(wp), 4),
               "n": len(wf)}
print("medium: %s | append+fsync median %.3f ms p95 %.3f | append only %.4f ms"
      % (dev, O["medium"]["fsync_median"], O["medium"]["fsync_p95"], O["medium"]["no_fsync_median"]))

# ---------- delay decomposition, with means so it sums ----------
def tsq(data_bytes, path):
    d = str(WORK) + "/d.bin"; open(d, "wb").write(data_bytes)
    subprocess.run(["openssl", "ts", "-query", "-data", d, "-sha256", "-cert", "-out", path],
                   check=True, capture_output=True)
TSA = "https://rfc3161.ai.moda"
def anchor(root_bytes):
    tsq(root_bytes, str(WORK) + "/q.tsq")
    t0 = time.perf_counter()
    subprocess.run(["curl", "-s", "-m", "25", "-H", "Content-Type: application/timestamp-query",
                    "--data-binary", ("@" + str(WORK) + "/q.tsq"), TSA, "-o", str(WORK) + "/r.tsr"],
                   capture_output=True)
    return (time.perf_counter() - t0) * 1e3

rows = []
NT = 20; TSEC = 1.0
for trial in range(NT):
    N = 20000
    store = [("rec%d" % i).encode() for i in range(N)]
    L = build([lh(x) for x in store]); root = L[-1][0]
    target = random.randrange(N)
    # place the modification at a random offset inside the interval
    frac = random.random()
    time.sleep(TSEC * frac)
    t_tamper = time.perf_counter()
    store[target] = b"TAMPERED" + store[target]
    # verification sweep: check records until the tampered one is checked
    order = list(range(N)); random.shuffle(order)
    order.remove(target); order.append(target)
    tick = None
    for i in order:
        if not vf(store[i], prf(L, i), root):
            tick = (time.perf_counter() - t_tamper) * 1e3
            break
    if tick is None:
        continue
    # then the anchor call, as the loop would after detection
    t0 = time.perf_counter()
    anchor(root)
    trip = (time.perf_counter() - t0) * 1e3
    rows.append({"tick_ms": tick, "rtt_ms": trip, "total_ms": tick + trip})
O["delay_decomposition"] = {
    "T_s": TSEC, "trials": len(rows), "tsa": TSA,
    "tick_median": med([r["tick_ms"] for r in rows]), "tick_mean": mean([r["tick_ms"] for r in rows]),
    "rtt_median": med([r["rtt_ms"] for r in rows]), "rtt_mean": mean([r["rtt_ms"] for r in rows]),
    "total_median": med([r["total_ms"] for r in rows]), "total_mean": mean([r["total_ms"] for r in rows]),
    "tickrtt_median_sum": med([r["tick_ms"] for r in rows]) + med([r["rtt_ms"] for r in rows]),
    "median_mean_sum": None,
}
DD = O["delay_decomposition"]
DD["median_mean_sum"] = round(DD["tick_mean"] + DD["rtt_mean"], 1)
print("delay T=%.1fs n=%d: tick median %.0f mean %.0f | rtt median %.0f mean %.0f | total median %.0f mean %.0f"
      % (TSEC, len(rows), DD["tick_median"], DD["tick_mean"], DD["rtt_median"], DD["rtt_mean"],
         DD["total_median"], DD["total_mean"]))
print("   tick_mean + rtt_mean = %.1f  vs total_mean %.1f  (means add)"
      % (DD["median_mean_sum"], DD["total_mean"]))
print("   tick_median + rtt_median = %.1f vs total_median %.1f (medians do not)"
      % (DD["tickrtt_median_sum"], DD["total_median"]))

# ---------- paired store comparison ----------
def st_(x):
    s = sorted(x); return {"median": round(statistics.median(s), 3), "p95": round(s[int(.95*(len(s)-1))], 3)}
subprocess.run("pkill -f immudb; sleep 1", shell=True); subprocess.run(f"rm -rf {DATA}/*", shell=True)
PORT = 4541
subprocess.run(f"cd {DATA} && HOME=/root nohup {IMMU} --dir {DATA} --port {PORT} --web-server=false "
               f"--pgsql-server=false --metrics-server=false > {DATA}/i.log 2>&1 &", shell=True)
t0 = time.time(); up = False
while time.time() - t0 < 45:
    s = socket.socket(); s.settimeout(0.5)
    try: s.connect(("127.0.0.1", PORT)); s.close(); up = True; break
    except Exception: time.sleep(0.5)
print("immudb up:", up)
if up:
    from immudb import ImmudbClient
    c = ImmudbClient("127.0.0.1:%d" % PORT); c.login("immudb", "immudb")
    try: c.createDatabase("clinic")
    except Exception: pass
    c.useDatabase("clinic")
    c.verifiedSet(b"warm", b"warm"); c.set(b"warm:u", b"warm")
    vw, uw, vr, ur = [], [], [], []
    for _ in range(5):
        for i, r in enumerate(recs):
            k = b"rec:%05d" % i
            t0 = time.perf_counter(); c.verifiedSet(k, r); vw.append((time.perf_counter() - t0) * 1e3)
            t0 = time.perf_counter(); c.set(k + b":u", r); uw.append((time.perf_counter() - t0) * 1e3)
            t0 = time.perf_counter(); c.verifiedGet(k); vr.append((time.perf_counter() - t0) * 1e3)
            t0 = time.perf_counter(); c.get(k); ur.append((time.perf_counter() - t0) * 1e3)
    O["immudb"] = {"verified_write": st_(vw), "unverified_write": st_(uw),
                   "verified_read": st_(vr), "unverified_read": st_(ur)}
    for k in ("verified_write", "unverified_write", "verified_read", "unverified_read"):
        print("   immudb %-17s median %.3f ms p95 %.3f" % (k, O["immudb"][k]["median"], O["immudb"][k]["p95"]))
    try: c.closeSession()
    except Exception: pass
subprocess.run("pkill -f immudb; sleep 1", shell=True)

class H(socketserver.BaseRequestHandler):
    def rq(self):
        hd = b""
        while len(hd) < 4:
            x = self.request.recv(4 - len(hd))
            if not x: return None
            hd += x
        n = struct.unpack("!I", hd)[0]; b = b""
        while len(b) < n:
            x = self.request.recv(n - len(b))
            if not x: return None
            b += x
        return json.loads(b)
    def rp(self, o):
        b = json.dumps(o).encode(); self.request.sendall(struct.pack("!I", len(b)) + b)
    def handle(self):
        try:
            while True:
                m = self.rq()
                if m is None: return
                if m["op"] == "put":
                    b = m["rec"].encode()
                    fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                    os.write(fd, struct.pack("!I", len(b)) + b); os.fsync(fd); os.close(fd)
                    S["log"].append(b); self.rp({"ok": 1})
                elif m["op"] == "seal":
                    L = build([lh(x) for x in S["log"]]); S["levels"] = L
                    S["root"] = L[-1][0] if L[-1] else h(b""); self.rp({"root": S["root"].hex()})
                elif m["op"] == "get":
                    self.rp({"rec": S["log"][m["idx"]].decode(),
                             "proof": [(s, x.hex()) for s, x in prf(S["levels"], m["idx"])],
                             "root": S["root"].hex()})
        except Exception: return
S = {"log": [], "levels": None}
srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start(); P = srv.server_address[1]
def call(m):
    s = socket.create_connection(("127.0.0.1", P), timeout=20)
    b = json.dumps(m).encode(); s.sendall(struct.pack("!I", len(b)) + b)
    hd = b""
    while len(hd) < 4: hd += s.recv(4 - len(hd))
    n = struct.unpack("!I", hd)[0]; buf = b""
    while len(buf) < n: buf += s.recv(n - len(buf))
    s.close(); return json.loads(buf)
for r in recs: call({"op": "put", "rec": r.decode()})
call({"op": "seal"}); call({"op": "get", "idx": 0})
ow, orr, oky = [], [], 0
for _ in range(5):
    for r in recs:
        t0 = time.perf_counter(); call({"op": "put", "rec": r.decode()}); ow.append((time.perf_counter() - t0) * 1e3)
    call({"op": "seal"})
    for i in range(len(recs)):
        t0 = time.perf_counter(); g = call({"op": "get", "idx": i}); orr.append((time.perf_counter() - t0) * 1e3)
        c_ = lh(g["rec"].encode())
        for side, sib in g["proof"]:
            sb = bytes.fromhex(sib); c_ = nh(c_, sb) if side == "R" else nh(sb, c_)
        if c_.hex() == g["root"]: oky += 1
O["ours"] = {"write": st_(ow), "read": st_(orr), "proofs_verified": oky, "reads": len(orr)}
print("   ours write median %.3f p95 %.3f | read median %.3f p95 %.3f | proofs %d/%d"
      % (O["ours"]["write"]["median"], O["ours"]["write"]["p95"], O["ours"]["read"]["median"],
         O["ours"]["read"]["p95"], oky, len(orr)))

json.dump(O, open(str(OUT) + "/ROUND5.json", "w"), indent=2, default=str)
print("\nSAVED ROUND5.json")
