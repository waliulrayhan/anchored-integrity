# Anchored Integrity and Automated Containment for Clinical Record Stores

Code, data loaders and result files behind the paper:

> **Anchored Integrity and Automated Containment for Clinical Record Stores**
> Md. Waliul Islam Rayhan, Department of Computer Science & Engineering, Jagannath University, Dhaka, Bangladesh

The paper gives a clinical record store an automated containment response whose evidence a write-path
attacker cannot forge: a Merkle log over record digests, whose root is published to an external RFC 3161
time-stamping authority, with containment triggered by cryptographic mismatch against a published root.
Every number in the paper is produced by the scripts here.

---

## Layout

```
.
├── scripts/        measurement scripts (one per table / experiment)
├── results/        the JSON each script writes — the numbers quoted in the paper
├── data/
│   ├── mimic/      MIMIC-III / MIMIC-IV demonstration CSVs (openly redistributable)
│   └── nslkdd/     NSL-KDD test files (third-party benchmark)
├── figures/        architecture diagram (PNG + the HTML that renders it)
├── paper/          paper.tex and paper.pdf
├── docs/           rendered architecture and literature-review pages
└── work/           scratch output created at run time (git-ignored)
```

## Requirements

Python 3.11+ with `numpy`, `scikit-learn`, `cryptography` and `immudb-py`:

```bash
pip install -r requirements.txt
```

Two things are needed beyond Python:

- **`openssl`** (any 3.x) — used for RFC 3161 timestamp queries and token verification.
- **`immudb` v1.11** — a static binary, needed only for the baseline table and the tamper test.
  Download it from the immudb releases page and either put it on `PATH` or set `IMMUDB_BIN`.
  Run it with the extra servers off to avoid port collisions:

  ```bash
  immudb --dir ./work/immu --port 3322 \
         --web-server=false --pgsql-server=false --metrics-server=false
  ```

## Data

Both datasets used here are **openly redistributable** and need no credentialing. Only the *full* MIMIC
databases require PhysioNet credentialing; the **demonstration** subsets ship with this repository and are
real, de-identified patient records.

```bash
python scripts/fetch_data.py          # re-download the demos, checks sha256 where known
```

Sources:

| Dataset | Origin | Notes |
|---|---|---|
| MIMIC-IV demo 2.2 | `physionet.org/files/mimic-iv-demo/2.2/hosp/` | `admissions`, `diagnoses_icd`, `labevents`, `prescriptions`, `patients` (`.csv.gz`) |
| MIMIC-III demo 1.4 | PhysioNet | `ADMISSIONS`, `DIAGNOSES_ICD`, `LABEVENTS`, `PATIENTS` |
| NSL-KDD | `github.com/defcom17/NSL_KDD` | `KDDTest+.txt`, `KDDTrain+_20Percent.txt` |

MIMIC supplies record size, field structure and the shape of the clinical access workload. It supports no
claim about detection quality. NSL-KDD is dated and non-clinical, and is used only as a reproducible source
of realistic flow-feature distributions.

## Running

Scripts resolve their paths relative to the repository root and can be overridden by environment variable:

```bash
export DATA_DIR=./data/mimic
export NSLKDD_DIR=./data/nslkdd
export RESULTS_DIR=./results
export WORK_DIR=./work
```

### The script behind each table

| Paper artifact | Script | Result file |
|---|---|---|
| Table II — Merkle scale (tree build, proof gen/verify, audit) | `scripts/r5all.py` | `results/ROUND5.json` |
| Component costs (digest, verify, detect–alert–contain, PBKDF2) | `scripts/r5all.py`, `scripts/r5lock2.py` | `results/ROUND5.json`, `results/round5_lock.json` |
| Table III — Merkle failing span (one rule per `n`) | `scripts/span_ii.py`, `scripts/merkle_fail.py` | `results/span_ii.json`, `results/merkle_fail.json` |
| Table IV — anchor round trip and measured detection delay | `scripts/r5delay2.py`, `scripts/r5token.py` | `results/round5_delay2.json`, `results/round5_token.json` |
| TSA token chain validation (12/12 OK, 12/12 wrong-data rejected) | `scripts/r5token.py` | `results/round5_token.json` |
| Sec. VI-C — re-anchoring race over 300 positions | `scripts/s4.py` | `results/stage4.json` |
| Sec. VI-C — bisection localisation (5 randomised trials) | `scripts/span_ii.py` | `results/span_ii.json` |
| Table V — gating on one clinical population | `scripts/s3d.py` | `results/apples.json` |
| Classifier in the loop (4,000-flow and combined scenarios) | `scripts/s3a.py`, `scripts/s3b.py` | `results/s3a.json`, `results/s3b.json` |
| Sec. VI-E — out-of-domain verifier (3 conditions × 40 trials) | `scripts/stage2.py`, `results/m3.json` | `results/stage2.json`, `results/m3.json` |
| Table VI — both stores client-server (verified and unverified writes) | `scripts/r5all.py` | `results/ROUND5.json` |
| Storage medium (ext4, fsync cost) | `scripts/r5all.py` | `results/ROUND5.json` |
| immudb tamper test (flipped byte in the value log) | `scripts/tamper2.py` | `results/baseline_result.json` |

Earlier rounds are kept for comparison — `results/FINAL4.json`, `results/locked4.json`,
`results/paired4.json`, `results/tableII.json` and their scripts. They used a different Merkle module and
their timings differ slightly; the paper quotes the round-5 files only.

## Correctness notes

- **Proof-length convention.** The paper reports a 20-hash inclusion proof at `n = 10^6`. RFC 6962's
  original text bounds a proof by `ceil(log2 n) + 1`, but that counts the root; the implementation returns
  the sibling path only, hence 20 rather than 21.
- **The Merkle construction is RFC 6962's.** The `0x00` / `0x01` domain separation and the leaf-domain rule
  come from RFC 6962 (Laurie, Langley, Kasper, June 2013); RFC 9162 supersedes it with the same construction.
- **`span_ii.py` and `merkle_fail.py`** show that the false-failure set from naive node duplication is
  exactly the span of the last unpaired node at the highest odd level, and that RFC 6962's promotion rule
  removes it (0 false failures from `n = 1` to `n = 10^6`).
- **`r5delay2.py` self-tests** its harness before measuring: honest proofs verify, a modified leaf fails.
- **Means and medians.** The detection-delay decomposition is reported with *means*, because means add and
  medians do not. Both are in `results/round5_delay2.json`.
- **Run-to-run variance.** Timings come from one two-vCPU container with loopback traffic, and repeated runs
  varied by up to a factor of two on the same container. Treat absolute values as indicative of the
  relationships, not as hardware-independent constants.
- **`delay_final.py` is superseded.** Its "tick" measurement timed a scan for the tamper rather than the wait
  to the next verification sweep, which made every interval look ~415 ms. `r5delay2.py` is the corrected
  harness, and its numbers are the ones in the paper.

## Reproducing end to end

```bash
python scripts/fetch_data.py
python scripts/r5all.py          # Tables II and VI, component costs, storage medium
python scripts/r5delay2.py       # Table IV
python scripts/r5token.py        # anchor round trip, connection reuse, chain validation
python scripts/span_ii.py        # Table III
python scripts/merkle_fail.py    # Merkle failure-set confirmation
python scripts/s4.py             # re-anchoring race
python scripts/s3d.py            # gating on one population
python scripts/s3a.py && python scripts/s3b.py   # classifier in the loop
```

Network-dependent scripts (`r5all.py`, `r5delay2.py`, `r5token.py`, `stage2.py`) call a public TSA and will
fail without outbound HTTPS. They still write their local measurements.

## Integrity, scope and what this code does not do

- The guarantee is narrow: a modification that changes **stored bytes** is detected. Exfiltration, ransomware
  without a retained comparison, and misuse of legitimate credentials are out of scope — none produces a
  digest mismatch.
- **Containment depends on an out-of-domain verifier.** An in-domain attacker who controls the code can
  suppress its own containment. The prototype in this repo runs the verifier as a separate process on the
  same host, which demonstrates that the detection logic does not depend on store-side state — it is *not* a
  genuinely separate trust domain.
- **The prefix check does not scale to short intervals at large stores.** It recomputes the root over the
  first `k` leaves, which is `O(k)`; consistency proofs were not implemented. A full audit costs 0.67 s at
  `10^6` records, longer than the 0.2 s interval, so the short-interval and large-store results are not
  combined.
- **The clinical access population is simulated** from MIMIC-IV field structure and admission distributions,
  labelled by construction (80 % subtle tampers, 20 % obvious). The score gate's recall partly reflects that
  construction.
- The access layer is password authentication with role-based authorisation and lockout. It is **not** a
  zero-trust architecture in the NIST SP 800-207 sense.

## Citation

`CITATION.cff` is included. Until the paper appears, please cite the repository:

```bibtex
@misc{rayhan2026anchored,
  title  = {Anchored Integrity and Automated Containment for Clinical Record Stores},
  author = {Rayhan, Md. Waliul Islam},
  year   = {2026},
  note   = {Code and data: \url{https://github.com/waliulrayhan/anchored-integrity}}
}
```

## Licence

Code is released under the MIT Licence (`LICENSE`). MIMIC demonstration data and NSL-KDD are third-party and
carry their own terms — see `data/README.md`. They are included here only because both are openly
redistributable.
