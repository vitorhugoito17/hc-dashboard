#!/bin/sh
# Publica o que a rodada produziu. O .cache (ZIPs da ANS e do CNES, ~1,5 GB)
# fica de fora de proposito: e insumo, nao resultado.
git config user.name github-actions
git config user.email actions@github.com

for f in dados.json agregado_ans.json cnes_map.json diagnostico_cnes.json diagnostico_cnj.json; do
  if [ -f "$f" ]; then git add "$f"; fi
done

git diff --staged --quiet || git commit -m "atualiza base do dashboard"
git push
