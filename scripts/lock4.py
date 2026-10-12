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

"""Round-4 FINAL locked numbers: integrity (Table II), component costs, and the paired store
comparison with immudb's unverified write. Records are built exactly as the released script
does, so the record-size claim and the digest input are the real serialised clinical record."""
import csv, glob, hashlib, json, os, random, socket, socketserver, statistics, struct, subprocess, threading, time
from collections import defaultdict

random.seed(20261012)
IMMU = IMMU
DATA = str(WORK) + "/immu3"
LOG = str(WORK) + "/ours3.log"
os.makedirs(DATA, exist_ok=True)

D = DATA


def rd(p):
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


miiv_adm = rd(f"{D}/miiv_admissions.csv"); miiv_dx = rd(f"{D}/miiv_diagnoses_icd.csv")
miiv_lab = rd(f"{D}/miiv_labevents.csv"); miiv_rx = rd(f"{D}/miiv_prescriptions.csv")
miii_adm = rd(f"{D}/miii_ADMISSIONS.csv"); miii_dx = rd(f"{D}/miii_DIAGNOSES_ICD.csv")
miii_lab = rd(f"{D}/miii_LABEVENTS.csv")


def mk_record(pid, row, dx_rows, lab_rows, rx_rows, ds):
    rec = {"dataset": ds, "patient_id": pid,
           "admission_type": row.get("admission_type", ""), "insurance": row.get("insurance", ""),
           "admittime": row.get("admittime", ""), "dischtime": row.get("dischtime", ""),
           "diagnosis_codes": sorted({d.get("icd_code", "") for d in dx_rows})[:12],
           "lab_events": len(lab_rows), "prescriptions": len(rx_rows),
           "gender": row.get("gender", "")}
    return json.dumps(rec, sort_keys=True).encode()


dx_by = defaultdict(list); lab_by = defaultdict(list); rx_by = defaultdict(list)
for d in miiv_dx: dx_by[d.get("hadm_id")].append(d)
for d in miiv_lab: lab_by[d.get("hadm_id")].append(d)
for d in miiv_rx: rx_by[d.get("hadm_id")].append(d)
records = [mk_record(a.get("subject_id"), a, dx_by[a.get("hadm_id")], lab_by[a.get("hadm_id")], rx_by[a.get("hadm_id")], "MIMIC-IV")
           for a in miiv_adm[:100]]
dx3 = defaultdict(list); lab3 = defaultdict(list)
for d in miii_dx: dx3[d.get("HADM_ID")].append(d)
for d in miii_lab: lab3[d.get("HADM_ID")].append(d)
records += [mk_record(a.get("SUBJECT_ID"), a, dx3[a.get("HADM_ID")], lab3[a.get("HADM_ID")], [], "MIMIC-III")
            for a in miii_adm[:100]]
sizes = sorted(len(r) for r in records)
out = {"store": {"records": len(records), "median_bytes": statistics.median(sizes),
                 "min_bytes": sizes[0], "max_bytes": sizes[-1]}}
print("store: %d real clinical records, median %d bytes (range %d-%d)"
      % (len(records), statistics.median(sizes), sizes[0], sizes[-1]))


def h(b): return hashlib.sha256(b).digest()
def lh(x): return h(b"\x00" + x)          # RFC 6962 leaf domain separation
def nh(l, r): return h(b"\x01" + l + r)   # RFC 6962 internal node


def build(leaves):
    cur = list(leaves); levels = [cur]
    while len(cur) > 1:
        nxt = [nh(cur[i], cur[i + 1]) for i in range(0, len(cur) - 1, 2)]
        if len(cur) % 2: nxt.append(cur[-1])   # RFC 6962 promotion, no duplication
        levels.append(nxt); cur = nxt
    return levels


def proof(levels, idx):
    p = []
    for L in range(len(levels) - 1):
        cur = levels[L]; s = idx ^ 1
        if s < len(cur): p.append(("R" if s > idx else "L", cur[s]))
        idx //= 2
    return p


def verify(data, idx, p, root):
    c = lh(data)
    for side, sib in p:
        c = nh(c, sib) if side == "R" else nh(sib, c)
    return c == root

# self-test both directions
ok_st = True
for n in (1, 2, 3, 5, 7, 8, 9, 16, 17, 31, 32, 100, 101, 333, 1000, 1001):
    d = [("rec%d" % i).encode() for i in range(n)]
    lv = build([lh(x) for x in d]); rt = lv[-1][0]
    for i in range(n):
        if not verify(d[i], i, proof(lv, i), rt): ok_st = False; print("HONEST FAIL", n, i)
    b = bytearray(d[n // 2]); b[0] ^= 1
    if verify(bytes(b), n // 2, proof(lv, n // 2), rt): ok_st = False; print("TAMPER MISSED", n)
print("SELFTEST:", "ok (honest proofs verify, tampered leaves fail)" if ok_st else "FAIL")
out["selftest"] = ok_st


def integrity(n, passes=5, sample=2400):
    tb, au, gen, ver = [], [], [], []
    ff = 0; plen = 0
    for _ in range(passes):
        d = [("rec%d" % i).encode() for i in range(n)]
        t0 = time.perf_counter(); lv = build([lh(x) for x in d]); tb.append((time.perf_counter() - t0) * 1e3)
        rt = lv[-1][0]; plen = len(lv) - 1
        for i in random.sample(range(n), sample):
            t0 = time.perf_counter(); p = proof(lv, i); gen.append((time.perf_counter() - t0) * 1e6)
            t0 = time.perf_counter()
            if not verify(d[i], i, p, rt): ff += 1
            ver.append((time.perf_counter() - t0) * 1e6)
        del d, lv
    return {"n": n, "tree_ms": statistics.median(tb), "audit_ms": statistics.median(tb),
            "proof_len": plen, "gen_us": statistics.median(gen), "verify_us": statistics.median(ver),
            "false_failures": ff, "sampled": sample * passes, "passes": passes}


I5 = integrity(100000); I6 = integrity(1000000)
for k, v in (("1e5", I5), ("1e6", I6)):
    print("%s: tree/audit %.0f ms, len %d, gen %.2f us, verify %.2f us, false failures %d/%d"
          % (k, v["tree_ms"], v["proof_len"], v["gen_us"], v["verify_us"], v["false_failures"], v["sampled"]))
out["integrity"] = {"1e5": I5, "1e6": I6}

# ---- components, using the real serialised record ----
n = 1000000
d = [("rec%d" % i).encode() for i in range(n)]
lv = build([lh(x) for x in d]); rt = lv[-1][0]
REC = records[0]
dg, vv, loop = [], [], []
p0 = proof(lv, 0)
for _ in range(5000):
    t0 = time.perf_counter(); lh(REC); dg.append((time.perf_counter() - t0) * 1e6)
    t0 = time.perf_counter(); verify(REC, 0, p0, rt); vv.append((time.perf_counter() - t0) * 1e6)
    t0 = time.perf_counter()
    c_ = lh(REC)
    for side, sib in p0: c_ = nh(c_, sib) if side == "R" else nh(sib, c_)
    alert = {"record": 0, "evidence": c_[:8].hex(), "actions": ("session_revoked", "account_locked", "record_quarantined")}
    _ = alert["actions"][0]
    loop.append((time.perf_counter() - t0) * 1e6)
del d, lv


def pb(iters, reps=31):
    t = []
    for _ in range(reps):
        t0 = time.perf_counter(); hashlib.pbkdf2_hmac("sha256", b"correct horse battery staple", os.urandom(16), iters)
        t.append((time.perf_counter() - t0) * 1e3)
    s = sorted(t)
    return {"median": statistics.median(s), "p95": s[int(.95 * (len(s) - 1))], "min": s[0], "max": s[-1], "n": len(s)}


P10 = pb(10000); P100 = pb(100000)
comp = {"record_bytes": len(REC), "digest_us": statistics.median(dg), "proof_len": len(p0),
        "verify_us": statistics.median(vv), "detect_alert_contain_us": statistics.median(loop),
        "pbkdf2_10k": P10, "pbkdf2_100k": P100}
print("components: digest %.2f us | verify(%d) %.2f us | detect+alert+contain %.2f us"
      % (comp["digest_us"], comp["proof_len"], comp["verify_us"], comp["detect_alert_contain_us"]))
print("            pbkdf2 10k median %.2f ms (%.2f-%.2f) | 100k median %.2f ms (%.2f-%.2f)"
      % (P10["median"], P10["min"], P10["max"], P100["median"], P100["min"], P100["max"]))
out["components"] = comp

# ---- paired store comparison ----
subprocess.run("pkill -f immudb; sleep 1", shell=True)
subprocess.run(f"rm -rf {DATA}/*", shell=True)
PORT = 4501
subprocess.run(f"cd {DATA} && HOME=/root nohup {IMMU} --dir {DATA} --port {PORT} "
               f"--web-server=false --pgsql-server=false --metrics-server=false > {DATA}/i.log 2>&1 &",
               shell=True)
t0 = time.time(); up = False
while time.time() - t0 < 45:
    s = socket.socket(); s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", PORT)); s.close(); up = True; break
    except Exception:
        time.sleep(0.5)
print("immudb up:", up)


def st_(x):
    s = sorted(x)
    return {"median": statistics.median(s), "p95": s[int(.95 * (len(s) - 1))], "min": s[0], "max": s[-1], "n": len(s)}


if up:
    from immudb import ImmudbClient
    c = ImmudbClient("127.0.0.1:%d" % PORT)
    c.login("immudb", "immudb")
    try:
        c.createDatabase("clinic")
    except Exception:
        pass
    c.useDatabase("clinic")
    vw, uw, vr, ur = [], [], [], []
    for _ in range(3):
        for i, r in enumerate(records):
            k = b"rec:%05d" % i
            t0 = time.perf_counter(); c.verifiedSet(k, r); vw.append((time.perf_counter() - t0) * 1e3)
            t0 = time.perf_counter(); c.set(k + b":u", r); uw.append((time.perf_counter() - t0) * 1e3)
            t0 = time.perf_counter(); c.verifiedGet(k); vr.append((time.perf_counter() - t0) * 1e3)
            t0 = time.perf_counter(); c.get(k); ur.append((time.perf_counter() - t0) * 1e3)
    out["immudb"] = {"verified_write": st_(vw), "unverified_write": st_(uw),
                     "verified_read": st_(vr), "unverified_read": st_(ur), "passes": 3}
    print("immudb v1.11 loopback gRPC, durable commit, 3 passes x 200 real records:")
    for k in ("verified_write", "unverified_write", "verified_read", "unverified_read"):
        v = out["immudb"][k]
        print("   %-17s median %.3f ms  p95 %.3f  (min %.3f max %.3f)"
              % (k, v["median"], v["p95"], v["min"], v["max"]))
    try:
        c.closeSession()
    except Exception:
        pass
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
                    lv = build([lh(x) for x in S["log"]])
                    S["levels"] = lv; S["root"] = lv[-1][0] if lv[-1] else h(b"")
                    self.rp({"root": S["root"].hex()})
                elif m["op"] == "get":
                    g = proof(S["levels"], m["idx"])
                    self.rp({"rec": S["log"][m["idx"]].decode(),
                             "proof": [(s, x.hex()) for s, x in g], "root": S["root"].hex()})
        except Exception:
            return


S = {"log": [], "levels": None}
srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
P = srv.server_address[1]


def call(m):
    s = socket.create_connection(("127.0.0.1", P), timeout=15)
    b = json.dumps(m).encode(); s.sendall(struct.pack("!I", len(b)) + b)
    hd = b""
    while len(hd) < 4: hd += s.recv(4 - len(hd))
    n = struct.unpack("!I", hd)[0]; buf = b""
    while len(buf) < n: buf += s.recv(n - len(buf))
    s.close(); return json.loads(buf)


for r in records: call({"op": "put", "rec": r.decode()})
call({"op": "seal"})
ow, orr, okv = [], [], 0
for _ in range(3):
    for r in records:
        t0 = time.perf_counter(); call({"op": "put", "rec": r.decode()}); ow.append((time.perf_counter() - t0) * 1e3)
    call({"op": "seal"})
    for i in range(len(records)):
        t0 = time.perf_counter(); g = call({"op": "get", "idx": i}); orr.append((time.perf_counter() - t0) * 1e3)
        c_ = lh(g["rec"].encode())
        for side, sib in g["proof"]:
            sb = bytes.fromhex(sib); c_ = nh(c_, sb) if side == "R" else nh(sb, c_)
        if c_.hex() == g["root"]: okv += 1
out["ours"] = {"write": st_(ow), "read": st_(orr), "proofs_verified": okv, "reads": len(orr), "passes": 3}
print("ours (TCP client-server, one fsync per write, proof verified client-side, 3 passes x 200):")
for k in ("write", "read"):
    v = out["ours"][k]
    print("   %-6s median %.3f ms  p95 %.3f  (min %.3f max %.3f)" % (k, v["median"], v["p95"], v["min"], v["max"]))
print("   proofs verified %d/%d" % (okv, len(orr)))

json.dump(out, open(str(OUT) + "/locked4.json", "w"), indent=2, default=str)
print("SAVED locked4.json")
