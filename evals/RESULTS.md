# Eval results — 96 real postings

Label counts: citizenship_blocked {'True': 17, 'False': 79}; sponsorship {'none_mentioned': 80, 'no_sponsorship': 7, 'h1b': 7, 'opt_cpt': 2}; graduation_fit {'no': 6, 'yes': 9, 'unclear': 81}

### Rules (regex, free) — held-out test, n=30

| step | accuracy | notes |
|---|---|---|
| 1 citizenship | 97% | recall 100%, precision 83% |
| 2 sponsorship | 100% | 4-way: h1b / opt_cpt / none_mentioned / no_sponsorship |
| 3 graduation | 100% | yes / no / unclear, for Dec 2027 |

Misses (step · id · label → predicted · why the label):

- citizenship_blocked · `gh-epirus-6203947004` · False → True · ITAR is a duty of the role, not an applicant requirement

### Rules (regex, free) — dev (rules tuned on these), n=66

| step | accuracy | notes |
|---|---|---|
| 1 citizenship | 100% | recall 100%, precision 100% |
| 2 sponsorship | 100% | 4-way: h1b / opt_cpt / none_mentioned / no_sponsorship |
| 3 graduation | 98% | yes / no / unclear, for Dec 2027 |

Misses (step · id · label → predicted · why the label):

- graduation_fit · `gh-andurilindustries-4802146007` · no → unclear · Must be a U.S. Person (export control); full-time start 2026

