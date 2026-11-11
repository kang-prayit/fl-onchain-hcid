#!/bin/bash
# Rebuild v4 macros, figures, bibliography and PDF. Official v3 CSVs are read only; v4 adds
# results/gasgap_v4 (analysis of official gas CSVs) and results/ipfs_dagpath_v4 (new run).
set -euo pipefail
ROOT=/workspace/riset/ict-express-fl-onchain
cd "$ROOT"
"$ROOT/.venv/bin/python" scripts/analyse_gasgap_v4.py
"$ROOT/.venv/bin/python" paper/paper_numbers_v4.py > /dev/null
cd "$ROOT/paper"
"$ROOT/.venv/bin/python" make_figs.py
python3 refs_src.py main.tex
latexmk -pdf -interaction=nonstopmode -f main.tex
pdfinfo main.pdf | egrep 'Pages|Page size'
echo "overfull: $(grep -c 'Overfull' main.log || true)  warnings: $(grep -c 'Warning' main.log || true)"
pdftotext -f 1 -l 1 main.pdf - | awk '/^Abstract/{f=1;next} /^Keywords/{f=0} f' | tr '\n' ' ' | wc -w | sed 's/^/abstract words: /'
