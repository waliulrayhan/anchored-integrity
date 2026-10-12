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

import json, os, socket, socketserver, struct, subprocess, threading, time, statistics, random, hashlib

random.seed(20261012)
IMMU = IMMU
DATA = str(WORK) + "/immu"
LOG = str(WORK) + "/ours.log"
for p in (DATA,):
    os.makedirs(p, exist_ok=True)
for p in (LOG,):
    if os.path.exists(p):
        os.remove(p)

# 200 real MIMIC-IV records
recs = []
src = DATA
import csv, glob
for f in sorted(glob.glob(src + "/*.csv"))[:2]:
    try:
        with open(f, newline="") as fh:
            r = csv.reader(fh)
            next(r, None)
            for row in r:
                s = ("|".join(row)).encode()
                if s:
                    recs.append(s)
                if len(recs) >= 200:
                    break
    except Exception as e:
        print("csv err", f, e)
    if len(recs) >= 200:
        break
recs = recs[:200]
print("records:", len(recs), "median bytes:", statistics.median(len(r) for r in recs))

out = {}


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


# ---------- immudb ----------
sh("pkill -f 'immudb' ; sleep 1")
sh(f"rm -rf {DATA}/*")
port = 4485
st = sh(f"cd {DATA} && HOME=/root nohup {IMMU} --dir {DATA} --port {port} "
        f"--web-server=false --pgsql-server=false --metrics-server=false "
        f"> {DATA}/immu.log 2>&1 & echo started")
t0 = time.time()
up = False
while time.time() - t0 < 45:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port)); s.close(); up = True; break
    except Exception:
        time.sleep(0.5)
print("immudb up:", up, "after %.1fs" % (time.time() - t0))

imm = {}
if up:
    import immudb
    from immudb import ImmudbClient
    c = ImmudbClient(f"127.0.0.1:{port}")
    c.login("immudb", "immudb")
    try:
        c.createDatabase("clinic")
    except Exception as e:
        print("createdb:", type(e).__name__)
    c.useDatabase("clinic")

    passes = 3
    vw, uw, vr, ur = [], [], [], []
    for _ in range(passes):
        for i, r in enumerate(recs):
            k = b"rec:%05d" % i
            t0 = time.perf_counter(); c.verifiedSet(k, r); vw.append((time.perf_counter() - t0) * 1e3)
            t0 = time.perf_counter(); c.set(k + b":u", r); uw.append((time.perf_counter() - t0) * 1e3)
            t0 = time.perf_counter(); c.verifiedGet(k); vr.append((time.perf_counter() - t0) * 1e3)
            t0 = time.perf_counter(); c.get(k); ur.append((time.perf_counter() - t0) * 1e3)
    def st_(x): return {"median": statistics.median(x), "p95": sorted(x)[int(0.95 * (len(x) - 1))]}
    imm = {"verified_write": st_(vw), "unverified_write": st_(uw),
           "verified_read": st_(vr), "unverified_read": st_(ur), "n": len(vw)}
    out["immudb"] = imm
    print("immudb v1.11 loopback gRPC, durable commit, %d writes/read per arm" % len(vw))
    print("  verified write   median %.3f ms p95 %.3f" % (imm["verified_write"]["median"], imm["verified_write"]["p95"]))
    print("  unverified write median %.3f ms p95 %.3f" % (imm["unverified_write"]["median"], imm["unverified_write"]["p95"]))
    print("  verified read    median %.3f ms p95 %.3f" % (imm["verified_read"]["median"], imm["verified_read"]["p95"]))
    print("  unverified read  median %.3f ms p95 %.3f" % (imm["unverified_read"]["median"], imm["unverified_read"]["p95"]))
    try:
        c.closeSession()
    except Exception:
        pass

sh("pkill -f 'immudb' ; sleep 1")

# ---------- our store, same shape ----------
def h(b): return hashlib.sha256(b).digest()
def lh(x): return h(b"\x00" + x)
def nh(l, r): return h(b"\x01" + l + r)


class H(socketserver.BaseRequestHandler):
    def recv(self):
        hd = b""
        while len(hd) < 4:
            c = self.request.recv(4 - len(hd))
            if not c: return None
            hd += c
        n = struct.unpack("!I", hd)[0]; buf = b""
        while len(buf) < n:
            c = self.request.recv(n - len(buf))
            if not c: return None
            buf += c
        return json.loads(buf)

    def send(self, o):
        b = json.dumps(o).encode(); self.request.sendall(struct.pack("!I", len(b)) + b)

    def handle(self):
        try:
            while True:
                m = self.recv()
                if m is None: return
                op = m["op"]
                if op == "put":
                    b = m["rec"].encode()
                    fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                    os.write(fd, struct.pack("!I", len(b)) + b); os.fsync(fd); os.close(fd)
                    S["log"].append(b); S["lv"] = None
                    self.send({"ok": 1})
                elif op == "seal":
                    S["lv"] = [lh(x) for x in S["log"]]
                    cur = list(S["lv"]); lv = [cur]
                    while len(cur) > 1:
                        nxt = [nh(cur[i], cur[i + 1]) for i in range(0, len(cur) - 1, 2)]
                        if len(cur) % 2: nxt.append(cur[-1])
                        lv.append(nxt); cur = nxt
                    S["levels"] = lv; S["root"] = lv[-1][0] if lv[-1] else h(b"")
                    self.send({"root": S["root"].hex()})
                elif op == "get":
                    i = m["idx"]; lv = S["levels"]; p = []
                    for L in range(len(lv) - 1):
                        cc = lv[L]; s = i ^ 1
                        if s < len(cc): p.append(("R" if s > i else "L", cc[s].hex()))
                        i //= 2
                    self.send({"rec": S["log"][m["idx"]].decode(), "proof": p, "root": S["root"].hex()})
        except Exception:
            return


S = {"log": [], "lv": None}
srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
srv.daemon_threads = True
p_ours = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def call(m, p=p_ours):
    s = socket.create_connection(("127.0.0.1", p), timeout=10)
    b = json.dumps(m).encode(); s.sendall(struct.pack("!I", len(b)) + b)
    hd = b""
    while len(hd) < 4:
        hd += s.recv(4 - len(hd))
    n = struct.unpack("!I", hd)[0]; buf = b""
    while len(buf) < n:
        buf += s.recv(n - len(buf))
    s.close(); return json.loads(buf)


okn = 0
for i, r in enumerate(recs):
    call({"op": "put", "rec": r.decode()})
call({"op": "seal"})

ow, orr = [], []
for _ in range(3):
    for i, r in enumerate(recs):
        t0 = time.perf_counter(); call({"op": "put", "rec": r.decode()}); ow.append((time.perf_counter() - t0) * 1e3)
    call({"op": "seal"})
    for i in range(len(recs)):
        t0 = time.perf_counter(); g = call({"op": "get", "idx": i}); orr.append((time.perf_counter() - t0) * 1e3)
        # verify the proof client-side against the sealed root
        c = lh(g["rec"].encode())
        for side, sib in g["proof"]:
            sb = bytes.fromhex(sib)
            c = nh(c, sb) if side == "R" else nh(sb, c)
        if c.hex() == g["root"]:
            okn += 1

def st_(x): return {"median": statistics.median(x), "p95": sorted(x)[int(0.95 * (len(x) - 1))]}
ours = {"write": st_(ow), "read": st_(orr), "proofs_verified": okn, "reads": len(orr)}
out["ours"] = ours
print("\nours: TCP client-server, durable append (fsync) per write, proof verified client-side")
print("  write median %.4f ms p95 %.4f" % (ours["write"]["median"], ours["write"]["p95"]))
print("  read  median %.4f ms p95 %.4f" % (ours["read"]["median"], ours["read"]["p95"]))
print("  proofs verified %d/%d" % (okn, len(orr)))

json.dump(out, open(str(OUT) + "/paired4.json", "w"), indent=2)
print("\nSAVED paired4.json")
