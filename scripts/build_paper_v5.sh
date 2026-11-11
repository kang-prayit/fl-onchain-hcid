#!/bin/bash
# Rebuild v5 macros, figures, bibliography and PDF.
# Official v3 CSVs and v4 result dirs are read only. New measurements live in results/eq1_bound_v5/.
set -euo pipefail
ROOT=/workspace/riset/ict-express-fl-onchain
cd "$ROOT"
"$ROOT/.venv/bin/python" paper/paper_numbers_v5.py > /dev/null
cd "$ROOT/paper"
"$ROOT/.venv/bin/python" make_figs.py
python3 refs_src.py main.tex
latexmk -pdf -interaction=nonstopmode -f main.tex
pdfinfo main.pdf | egrep 'Pages|Page size'
echo "overfull: $(grep -c 'Overfull' main.log || true)  warnings: $(grep -c '^Warning\|LaTeX Warning' main.log || true)"
pdftotext -f 1 -l 1 main.pdf - | awk '/^Abstract/{f=1;next} /^Keywords/{f=0} f' | tr '\n' ' ' | wc -w | sed 's/^/abstract words: /'
cp -a main.pdf ICTExpress_FL_HCID_draft_v5_20261111.pdf
