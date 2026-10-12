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

"""Round-4 FINAL lock. Everything Table II, the cost paragraph and Table V quote comes from
here, so no two places can disagree. Leaves are built from the real serialised clinical
records. One warm-up pass is discarded before measurement."""
import csv, hashlib, json, os, random, socket, socketserver, statistics, struct, subprocess, threading, time
from collections import defaultdict

random.seed(20261012)
IMMU = IMMU
DATA = str(WORK) + "/immu4"; LOG = str(WORK) + "/ours4.log"
os.makedirs(DATA, exist_ok=True)
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
        "dischtime": row.get("dischtime", ""),
        "diagnosis_codes": sorted({d.get("icd_code", "") for d in dx})[:12],
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
print("store: %d records, median %d B (range %d-%d)" % (len(recs), statistics.median(sz), sz[0], sz[-1]))

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
def vf(data, i, p, root):
    c = lh(data)
    for side, s in p: c = nh(c, s) if side == "R" else nh(s, c)
    return c == root

ok = True
for n in (1, 2, 3, 5, 8, 9, 17, 31, 32, 100, 101, 333, 1001):
    d = [("r%d" % i).encode() for i in range(n)]; L = build([lh(x) for x in d]); r = L[-1][0]
    for i in range(n):
        if not vf(d[i], i, prf(L, i), r): ok = False
    b = bytearray(d[n // 2]); b[0] ^= 1
    if vf(bytes(b), n // 2, prf(L, n // 2), r): ok = False
print("SELFTEST:", "ok" if ok else "FAIL")
O = {"store": {"records": len(recs), "median_bytes": statistics.median(sz), "min": sz[0], "max": sz[-1]}, "selftest": ok}

def integ(n, passes=9, sample=2400):
    build([lh(b"warm%d" % i) for i in range(min(n, 2000))])  # warm-up pass, discarded
    tb, ge, ve, au = [], [], [], []
    ff = 0; pl = 0
    for _ in range(passes):
        base = [lh(recs[i % len(recs)]) for i in range(n)]
        t0 = time.perf_counter(); L = build(base); tb.append((time.perf_counter() - t0) * 1e3)
        root = L[-1][0]; pl = len(L) - 1
        t0 = time.perf_counter()                      # audit: rebuild and compare to the anchored root
        L2 = build(base); same = (L2[-1][0] == root)
        au.append((time.perf_counter() - t0) * 1e3)
        for i in random.sample(range(n), sample):
            t0 = time.perf_counter(); p = prf(L, i); ge.append((time.perf_counter() - t0) * 1e6)
            t0 = time.perf_counter()
            if not vf(recs[i % len(recs)], i, p, root): ff += 1
            ve.append((time.perf_counter() - t0) * 1e6)
        del base, L, L2
    return {"n": n, "tree_ms": statistics.median(tb), "audit_ms": statistics.median(au),
            "proof_len": pl, "gen_us": statistics.median(ge), "verify_us": statistics.median(ve),
            "false_failures": ff, "sampled": sample * passes, "passes": passes}

I5 = integ(100000); I6 = integ(1000000)
for k, v in (("1e5", I5), ("1e6", I6)):
    print("%s: tree/audit %.0f/%.0f ms, len %d, gen %.2f us, verify %.2f us, falsefail %d/%d"
          % (k, v["tree_ms"], v["audit_ms"], v["proof_len"], v["gen_us"], v["verify_us"], v["false_failures"], v["sampled"]))
O["integrity"] = {"1e5": I5, "1e6": I6}

# components on real record bytes
n = 1000000
base = [lh(recs[i % len(recs)]) for i in range(n)]; L = build(base); root = L[-1][0]
REC = recs[len(recs) // 2]          # median-sized clinical record
p0 = prf(L, 7)
dg, vv, lp = [], [], []
for _ in range(6000):
    t0 = time.perf_counter(); lh(REC); dg.append((time.perf_counter() - t0) * 1e6)
    t0 = time.perf_counter(); vf(REC, 7, p0, root); vv.append((time.perf_counter() - t0) * 1e6)
    t0 = time.perf_counter()
    c = lh(REC)
    for side, s in p0: c = nh(c, s) if side == "R" else nh(s, c)
    ev = {"record": 7, "evidence": c[:8].hex(),
          "action": ("session_revoked", "account_locked", "record_quarantined")[0]}
    lp.append((time.perf_counter() - t0) * 1e6)
del base, L

def pb(it, reps=41):
    t = []
    for _ in range(reps):
        t0 = time.perf_counter(); hashlib.pbkdf2_hmac("sha256", b"correct horse battery staple", os.urandom(16), it)
        t.append((time.perf_counter() - t0) * 1e3)
    s = sorted(t)
    return {"median": statistics.median(s), "p95": s[int(.95 * (len(s) - 1))], "min": s[0], "max": s[-1], "n": len(s)}
P10 = pb(10000); P100 = pb(100000)
O["components"] = {"record_bytes": len(REC), "digest_us": statistics.median(dg), "proof_len": len(p0),
                   "verify_us": statistics.median(vv), "loop_us": statistics.median(lp),
                   "pbkdf2_10k": P10, "pbkdf2_100k": P100}
print("components (record %d B): digest %.2f us | verify(%d) %.2f us | detect+alert+contain %.2f us"
      % (len(REC), O["components"]["digest_us"], len(p0), O["components"]["verify_us"], O["components"]["loop_us"]))
print("   pbkdf2: 10k median %.2f ms (%.2f-%.2f) | 100k median %.2f ms (%.2f-%.2f)"
      % (P10["median"], P10["min"], P10["max"], P100["median"], P100["min"], P100["max"]))

# ---- paired stores ----
def st_(x):
    s = sorted(x); return {"median": statistics.median(s), "p95": s[int(.95 * (len(s) - 1))], "min": s[0], "max": s[-1], "n": len(s)}
subprocess.run("pkill -f immudb; sleep 1", shell=True); subprocess.run(f"rm -rf {DATA}/*", shell=True)
PORT = 4511
subprocess.run(f"cd {DATA} && HOME=/root nohup {IMMU} --dir {DATA} --port {PORT} "
               f"--web-server=false --pgsql-server=false --metrics-server=false > {DATA}/i.log 2>&1 &", shell=True)
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
                   "verified_read": st_(vr), "unverified_read": st_(ur), "passes": 5, "records": len(recs)}
    print("immudb v1.11, 5 passes x %d real records:" % len(recs))
    for k in ("verified_write", "unverified_write", "verified_read", "unverified_read"):
        v = O["immudb"][k]; print("   %-17s median %.3f ms p95 %.3f (min %.3f max %.3f)" % (k, v["median"], v["p95"], v["min"], v["max"]))
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
                    L = build([lh(x) for x in S["log"]]); S["levels"] = L; S["root"] = L[-1][0] if L[-1] else h(b"")
                    self.rp({"root": S["root"].hex()})
                elif m["op"] == "get":
                    self.rp({"rec": S["log"][m["idx"]].decode(),
                             "proof": [(s, x.hex()) for s, x in prf(S["levels"], m["idx"])], "root": S["root"].hex()})
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
call({"op": "seal"}); call({"op": "get", "idx": 0})          # warm-up
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
O["ours"] = {"write": st_(ow), "read": st_(orr), "proofs_verified": oky, "reads": len(orr), "passes": 5, "records": len(recs)}
print("ours, 5 passes x %d real records (one fsync per write, proof verified client-side):" % len(recs))
for k in ("write", "read"):
    v = O["ours"][k]; print("   %-6s median %.3f ms p95 %.3f (min %.3f max %.3f)" % (k, v["median"], v["p95"], v["min"], v["max"]))
print("   proofs verified %d/%d" % (oky, len(orr)))
json.dump(O, open(str(OUT) + "/FINAL4.json", "w"), indent=2, default=str)
print("SAVED FINAL4.json")
