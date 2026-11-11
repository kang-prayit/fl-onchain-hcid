# Manuscript sources

These files rebuild the ICT Express draft *Constant on-chain commitment for federated learning rounds via hierarchical content identifiers* with `elsarticle` `[final,3p,times,twocolumn]`.

```bash
# from the repository root, after `pip install -r requirements.txt`
python paper/paper_numbers_v5.py
python paper/make_figs.py
# compile paper/main.tex with latexmk / pdflatex
```

Numbers come from `../results/` via `\\Nm{Name}` macros. Do not edit `numbers_macros.tex` by hand.
