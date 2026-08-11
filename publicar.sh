git config user.name github-actions
git config user.email actions@github.com
git add dados.json
git diff --staged --quiet || git commit -m atualiza-base-do-dashboard
git push
