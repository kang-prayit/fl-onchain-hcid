#!/bin/bash
# Rebuild v3 tables, figures, bibliography, and PDF. Run after timing_clean_v3 + tamper_v3 (+ optional two-peer).
set -euo pipefail
ROOT=/workspace/riset/ict-express-fl-onchain
export PATH="$ROOT/tools/kubo:$HOME/.foundry/bin:$PATH"
cd "$ROOT"
"$ROOT/.venv/bin/python" scripts/aggregate_v3.py
"$ROOT/.venv/bin/python" paper/paper_numbers_v3.py
cd "$ROOT/paper"
"$ROOT/.venv/bin/python" make_figs.py
"$ROOT/.venv/bin/python" refs_src.py main.tex
latexmk -pdf -interaction=nonstopmode -f main.tex
pdfinfo main.pdf | egrep 'Pages|Page size'
echo '--- overfull ---'
grep -c 'Overfull' main.log || true
grep 'Overfull' main.log || echo 'no overfull boxes'
# abstract word count
python3 - << 'PY'
import re
t=open("main.tex").read()
m=re.search(r"%ABSTRACT-START(.*)%ABSTRACT-END", t, re.S)
# expand macros? count source tokens after a crude macro strip is misleading.
# Use pdftotext of page 1 abstract region later.
body=re.sub(r"\\[A-Za-z]+"," ", m.group(1))
body=re.sub(r"[{}$\\]"," ", body)
print("rough source tokens (macros not expanded):", len(body.split()))
PY
pdftotext -f 1 -l 1 main.pdf - | head
