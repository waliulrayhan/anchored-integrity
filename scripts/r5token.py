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

"""Round-5 token evidence, done cleanly: (1) chain validation with the token's own DigiCert chain,
(2) imprint binding parsed correctly, (3) fresh-connection vs keep-alive anchor cost."""
import hashlib, json, statistics, subprocess, tempfile, time
from pathlib import Path
TMP = Path(tempfile.mkdtemp(dir="/scratch")); OUT = {}
def h(b): return hashlib.sha256(b).digest()
def run(a, inp=None, text=True):
    return subprocess.run(a, input=inp, capture_output=True, text=text)

def curl(url, out, extra=None):
    a = ["curl", "-s", "-m", "25"] + (extra or []) + ["-o", str(out), "-w", "%{http_code}", url]
    return subprocess.run(a, capture_output=True, text=True).stdout.strip()

data = TMP / "data.bin"; PAY = h(b"round5 imprint")
data.write_bytes(PAY)
run(["openssl", "ts", "-query", "-data", str(data), "-sha256", "-cert", "-out", str(TMP / "q.tsq")])
tsr = TMP / "r.tsr"
code = curl("https://rfc3161.ai.moda", tsr, ["-H", "Content-Type: application/timestamp-query",
                                             "--data-binary", f"@{TMP/'q.tsq'}"])
TOK = tsr.read_bytes(); OUT["token_bytes"] = len(TOK)
print("token HTTP %s, %d bytes" % (code, len(TOK)))

# ---------- 1. the imprint, from the raw token, no text-scraping ----------
run(["openssl", "ts", "-reply", "-in", str(tsr), "-token_out", "-out", str(TMP / "tok.der")])
txt = run(["openssl", "ts", "-reply", "-in", str(tsr), "-text"]).stdout
print("\n--- reply text, search for the message imprint ---")
keep = False
for ln in txt.split("\n"):
    if "Message" in ln or "Hash" in ln or "Status" in ln or "Policy" in ln or "Nonce" in ln:
        print("   " + ln.rstrip()[:110])

# locate the hex block after "Message data" / "Message digest", skipping the offset column
lines = txt.split("\n")
imps = []
for i, ln in enumerate(lines):
    if "Message data" in ln or "Message digest" in ln:
        buf = []
        for ln2 in lines[i + 1:i + 10]:
            s = ln2.rstrip()
            if not s.strip():
                break
            # drop a leading "0000 - " style offset
            if "-" in s:
                head, _, tail = s.partition("-")
                if all(c in "0123456789abcdefABCDEF" for c in head.strip()):
                    s = tail
            hexs = "".join(c for c in s if c in "0123456789abcdefABCDEF")
            if not hexs:
                break
            buf.append(hexs)
            if sum(len(b) for b in buf) >= 64:
                break
        cand = "".join(buf)[:64]
        if len(cand) >= 64:
            imps.append(cand)
OUT["imprints"] = imps
OUT["sha256_data"] = PAY.hex()
print("\nimprint candidates:", imps)
print("SHA-256(published data):", PAY.hex())
OUT["imprint_matches_data"] = any(c.lower() == PAY.hex() for c in imps)
print("imprint == SHA-256(published data):", OUT["imprint_matches_data"])

# ---------- 2. chain validation, clean ----------
pem = run(["openssl", "pkcs7", "-inform", "DER", "-in", str(TMP / "tok.der"), "-print_certs"], text=False)
pemb = pem.stdout or b""
(TMP / "all.pem").write_bytes(pemb)
OUT["certs_in_token"] = pemb.decode("utf-8", "replace").count("BEGIN CERTIFICATE")
print("\ncerts in token: %d" % OUT["certs_in_token"])
raw = pemb.decode("utf-8", "replace")
subj = [l for l in raw.split("\n") if l.strip().startswith("subject=")]
OUT["token_subjects"] = subj
for s in subj: print("   " + s.strip()[:110])

def tsverify(label, cafile, untrusted=True):
    a = ["openssl", "ts", "-verify", "-in", str(tsr), "-data", str(data), "-CAfile", str(cafile)]
    if untrusted: a += ["-untrusted", str(TMP / "all.pem")]
    v = run(a)
    msg = ((v.stdout or "") + (v.stderr or "")).strip()
    key = next((l for l in msg.split("\n") if "Verification" in l), msg.split("\n")[0] if msg else "")
    OUT[label] = {"rc": v.returncode, "msg": key[:160]}
    print("   %-28s rc=%d | %s" % (label, v.returncode, key[:90]))
    return v.returncode

print("\nchain validation (system trust store + the token's own intermediates):")
tsverify("verify_system_store", "/etc/ssl/certs/ca-certificates.crt", True)
(TMP / "bad.bin").write_bytes(h(b"not this"))
v = run(["openssl", "ts", "-verify", "-in", str(tsr), "-data", str(TMP / "bad.bin"),
         "-CAfile", "/etc/ssl/certs/ca-certificates.crt", "-untrusted", str(TMP / "all.pem")])
OUT["wrong_data_rc"] = v.returncode
print("   %-28s rc=%d" % ("wrong data (must fail)", v.returncode))

# ---------- 3. fresh connection vs keep-alive ----------
print("\nanchor cost, fresh connection per request vs one reused connection:")
res = {}
for label, extra in (("fresh", None), ("keepalive", ["--next", "-X", "POST", "-H",
                                                     "Content-Type: application/timestamp-query",
                                                     "--data-binary", f"@{TMP/'q.tsq'}",
                                                     "https://rfc3161.ai.moda"])):
    pass
# do it explicitly: fresh = one curl per request; keepalive = one curl with repeated --next blocks
lat_fresh = []
for i in range(20):
    run(["openssl", "ts", "-query", "-data", str(data), "-sha256", "-cert", "-out", str(TMP / "qf.tsq")])
    t0 = time.perf_counter()
    subprocess.run(["curl", "-s", "-m", "25", "-H", "Content-Type: application/timestamp-query",
                    "--data-binary", f"@{TMP/'qf.tsq'}", "https://rfc3161.ai.moda", "-o", str(TMP / "rf.tsr")],
                   capture_output=True)
    lat_fresh.append((time.perf_counter() - t0) * 1e3)
    time.sleep(0.15)
res["fresh"] = {"median": statistics.median(lat_fresh), "min": min(lat_fresh), "max": max(lat_fresh), "n": len(lat_fresh)}
print("   fresh connection each request : median %.1f ms (min %.1f max %.1f)"
      % (res["fresh"]["median"], res["fresh"]["min"], res["fresh"]["max"]))

# reuse: python http.client keeps the socket open
import http.client, ssl
lat_ka = []
conn = http.client.HTTPSConnection("rfc3161.ai.moda", timeout=25, context=ssl.create_default_context())
for i in range(20):
    run(["openssl", "ts", "-query", "-data", str(data), "-sha256", "-cert", "-out", str(TMP / "qk.tsq")])
    body = (TMP / "qk.tsq").read_bytes()
    t0 = time.perf_counter()
    try:
        conn.request("POST", "/", body=body, headers={"Content-Type": "application/timestamp-query",
                                                      "Content-Length": str(len(body))})
        r = conn.getresponse(); d = r.read()
    except Exception as e:
        conn = http.client.HTTPSConnection("rfc3161.ai.moda", timeout=25, context=ssl.create_default_context())
        continue
    lat_ka.append((time.perf_counter() - t0) * 1e3)
    time.sleep(0.15)
res["keepalive"] = {"median": statistics.median(lat_ka), "min": min(lat_ka), "max": max(lat_ka), "n": len(lat_ka)}
print("   one reused connection         : median %.1f ms (min %.1f max %.1f)"
      % (res["keepalive"]["median"], res["keepalive"]["min"], res["keepalive"]["max"]))
try: conn.close()
except Exception: pass
OUT["anchor_conn"] = res

# does the query carry a nonce?
q = run(["openssl", "ts", "-query", "-in", str(TMP / "q.tsq"), "-text"]).stdout
OUT["nonce_present"] = "Nonce" in q and "no_nonce" not in q.lower()
print("\nquery text carries a nonce:", OUT["nonce_present"])
for ln in q.split("\n"):
    if "Nonce" in ln or "Version" in ln or "Hash Algorithm" in ln:
        print("   " + ln.strip()[:100])

json.dump(OUT, open(str(OUT) + "/round5_token.json", "w"), indent=2, default=str)
print("\nSAVED round5_token.json")
