#!/usr/bin/env python3
"""Fetch the openly redistributable datasets used by this project.

MIMIC-IV / MIMIC-III *demonstration* subsets and NSL-KDD need no credentialing. Only the full MIMIC
databases do, and this project does not use them.

Usage:
    python scripts/fetch_data.py            # fetch anything missing
    python scripts/fetch_data.py --force    # refetch everything
"""
import argparse
import gzip
import hashlib
import os
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
MIMIC = Path(os.environ.get("DATA_DIR", ROOT / "data" / "mimic"))
NSLKDD = Path(os.environ.get("NSLKDD_DIR", ROOT / "data" / "nslkdd"))

MIV = "https://physionet.org/files/mimic-iv-demo/2.2/hosp"
MIII = "https://physionet.org/files/mimiciii-demo/1.4"

# destination name -> (url, is_gzip)
MIMIC_FILES = {
    "miiv_admissions.csv":     (f"{MIV}/admissions.csv.gz", True),
    "miiv_diagnoses_icd.csv":  (f"{MIV}/diagnoses_icd.csv.gz", True),
    "miiv_labevents.csv":      (f"{MIV}/labevents.csv.gz", True),
    "miiv_prescriptions.csv":  (f"{MIV}/prescriptions.csv.gz", True),
    "miiv_patients.csv":       (f"{MIV}/patients.csv.gz", True),
    # MIMIC-III demo 1.4 ships uncompressed CSVs under a versioned folder
    "miii_ADMISSIONS.csv":     (f"{MIII}/ADMISSIONS.csv", False),
    "miii_DIAGNOSES_ICD.csv":  (f"{MIII}/DIAGNOSES_ICD.csv", False),
    "miii_LABEVENTS.csv":      (f"{MIII}/LABEVENTS.csv", False),
    "miii_PATIENTS.csv":       (f"{MIII}/PATIENTS.csv", False),
}

NSLKDD_FILES = {
    "KDDTest+.txt":             "https://raw.githubusercontent.com/defcom17/NSL_KDD/master/KDDTest+.txt",
    "KDDTrain+_20Percent.txt":  "https://raw.githubusercontent.com/defcom17/NSL_KDD/master/KDDTrain%2B_20Percent.txt",
}


def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(buf)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def fetch(url, dest, is_gzip=False, force=False):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force and dest.stat().st_size > 0:
        print(f"  have   {dest.name:28s} {dest.stat().st_size:>10,} B")
        return True
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        print(f"  get    {dest.name:28s} <- {url}")
        with urllib.request.urlopen(url, timeout=90) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        if is_gzip:
            raw = dest.with_suffix(dest.suffix + ".gz")
            tmp.rename(raw)
            with gzip.open(raw, "rb") as g, open(dest, "wb") as o:
                shutil.copyfileobj(g, o)
            raw.unlink()
        else:
            tmp.rename(dest)
        print(f"         -> {dest.stat().st_size:,} B  sha256 {sha256(dest)[:16]}...")
        return True
    except Exception as e:
        print(f"  FAIL   {dest.name}: {e}")
        if tmp.exists():
            tmp.unlink()
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="refetch even if present")
    a = ap.parse_args()

    print("MIMIC demonstration subsets (open, no credentialing) ->", MIMIC)
    ok = sum(fetch(u, MIMIC / n, gz, a.force) for n, (u, gz) in MIMIC_FILES.items())

    print("\nNSL-KDD (third-party benchmark) ->", NSLKDD)
    ok += sum(fetch(u, NSLKDD / n, False, a.force) for n, u in NSLKDD_FILES.items())

    total = len(MIMIC_FILES) + len(NSLKDD_FILES)
    print(f"\n{ok}/{total} files present.")
    if ok < total:
        print("Some files could not be fetched. MIMIC-III demo URLs change between versions;")
        print("browse https://physionet.org/content/mimiciii-demo/ and place the CSVs in", MIMIC)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
