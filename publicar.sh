#!/bin/sh
# Publica o que a rodada produziu. O .cache (ZIPs da ANS e do CNES, ~1,5 GB)
# fica de fora de proposito: e insumo, nao resultado.
git config user.name github-actions
git config user.email actions@github.com

for f in dados.json agregado_ans.json cnes_map.json diagnostico_cnes.json \
         diagnostico_cnj.json Healthcare_Database_Dashboard.html; do
  if [ -f "$f" ]; then git add "$f"; fi
done

if git diff --staged --quiet; then
  echo "nada mudou nesta rodada"
  exit 0
fi
git commit -m "atualiza base do dashboard"

# se alguem tiver empurrado durante a rodada (o download da ANS leva 20 min),
# o push seria recusado; rebasear e tentar de novo resolve sem perder o commit
n=1
while [ $n -le 5 ]; do
  if git push; then
    echo "publicado na tentativa $n"
    exit 0
  fi
  echo "push recusado; rebaseando sobre o main e tentando de novo ($n/5)"
  git pull --rebase --autostash origin main || exit 1
  n=$((n + 1))
  sleep 3
done
echo "nao consegui publicar depois de 5 tentativas"
exit 1
