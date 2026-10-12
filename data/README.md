# Data

Both datasets here are **openly redistributable and need no credentialing**. Only the *full* MIMIC
databases require PhysioNet credentialing; the **demonstration** subsets are public and are real,
de-identified patient records.

## `mimic/` — MIMIC-III and MIMIC-IV demonstrations

De-identified demonstration subsets, used to size and shape the clinical record store: record size, field
structure, and the shape of the clinical access workload. They support **no** claim about detection quality.

| File | Source |
|---|---|
| `miiv_admissions.csv`, `miiv_diagnoses_icd.csv`, `miiv_labevents.csv`, `miiv_prescriptions.csv`, `miiv_patients.csv` | MIMIC-IV demo 2.2, `physionet.org/files/mimic-iv-demo/2.2/hosp/` |
| `miii_ADMISSIONS.csv`, `miii_DIAGNOSES_ICD.csv`, `miii_LABEVENTS.csv`, `miii_PATIENTS.csv` | MIMIC-III clinical database demo 1.4 |

The loaders read exactly the columns these files carry — e.g. `diagnoses_icd` needs
`subject_id, hadm_id, seq_num, icd_code, icd_version`, and `prescriptions` needs `hadm_id, drug`. If you
re-download from PhysioNet you get `.csv.gz`; decompress and keep the file names above.

Re-download with:

```bash
python scripts/fetch_data.py
```

## `nslkdd/` — NSL-KDD

Third-party benchmark (`github.com/defcom17/NSL_KDD`). Used only as a reproducible source of realistic
network-flow feature distributions for the score-gated baseline. It is over two decades old, is not
healthcare data, and its numbers are **not comparable** to the clinical session table in the paper.

## Terms

The MIMIC demonstration subsets are published by the MIT Laboratory for Computational Physiology via
PhysioNet for open research use. NSL-KDD is distributed for research use by its authors. Neither is covered
by this repository's MIT licence; consult each source's terms before redistributing.
