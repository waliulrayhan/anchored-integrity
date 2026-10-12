# Results

Every JSON here was written by a script in `scripts/`. The paper quotes the **round-5** files only; the
earlier rounds are kept so the revisions can be audited.

## Current (round 5) — what the paper quotes

| File | Written by | Holds |
|---|---|---|
| `ROUND5.json` | `r5all.py` | Table II (tree build, proof gen/verify, audit, false failures), component costs, PBKDF2, storage medium, Table VI both stores |
| `round5_delay2.json` | `r5delay2.py` | Table IV — detection delay, means and medians, plus the sweep cost |
| `round5_token.json` | `r5token.py` | anchor round trip, fresh-vs-reused connection, token chain validation |
| `round5_lock.json` | `r5lock2.py` | token validation counts (12/12 OK, 0/12 wrong-data) and a consistent component set |
| `round5_env.json` | `r5env` step in `r5token.py` | filesystem / medium evidence (ext4, `/dev/vda`, fsync cost) |
| `span_ii.json` | `span_ii.py` | Table III — unpaired-node spans, failing ranges, rates |
| `merkle_fail.json` | `merkle_fail.py` | confirmation that the failing set equals the span |
| `m3.json` | `stage2.py` | out-of-domain verifier (3 conditions × 40 trials) |
| `stage2.json` | `stage2.py` | TSA percentiles over 60 requests to each of two vendors |
| `stage4.json` | `s4.py` | re-anchoring race over 300 positions |
| `apples.json` | `s3d.py` | Table V — score gate vs deterministic rule (AUC 0.765) |
| `s3a.json`, `s3b.json` | `s3a.py`, `s3b.py` | NSL-KDD classifier; classifier in the loop; gating thresholds |
| `baseline_result.json` | `tamper2.py` | immudb tamper test — value-log corruption detected |

## Earlier rounds — superseded, kept for auditing

`FINAL4.json`, `locked4.json`, `paired4.json`, `tableII.json`, `final_table_v.json`, `scale_result.json`,
`delay_final.json`, `delay_result.json`, `stage1.json`, `immudb_tamper.json`, `crossref_all.json`,
`tamper_result.json`.

Two of these are worth naming because a reader may otherwise be confused:

- **`delay_final.json`** comes from a harness whose "tick" measurement timed *a scan for the tamper* rather
  than *the wait to the next verification sweep*. That made every interval look about 415 ms and is why the
  older figures (87 / 393 / 2323 ms) differ from the current ones (95 / 493 / 2488 ms). `r5delay2.py`
  supersedes it.
- **`stage1.json`** predates the corrected RFC 6962 leaf hashing and is superseded by `stage4.json`.

`crossref_all.json` records the bibliographic checks made against Crossref while preparing the paper.
