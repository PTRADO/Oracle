# 🎱 ORACLE v2.0 MAX — Loto + EuroMillions

Pronostiqueur auto-hébergé : 8 techniques folklore documentées + les 3 leviers
réels (anti-partage **calibré sur les vraies données de gagnants FDJ**, EV
jackpot, surveillance χ²), systèmes réducteurs à garantie vérifiée, mini-app
web glassmorphism, mise à jour automatique quotidienne. Zéro dépendance.

## Structure

```
oracle.py                         # moteur bi-jeux (Python stdlib)
MASTERPROMPT.md                   # ← à donner à Claude Code pour la suite
docs/index.html                   # la mini-app (1 fichier, vanilla)
docs/loto.json                    # pronostics Loto (généré)
docs/euromillions.json            # pronostics EuroMillions (généré)
data/                             # mirrors CSV (générés par le cron)
.github/workflows/pronostics.yml  # cron quotidien 04h30 UTC
```

## Démarrage local (1 minute)

```bash
python3 oracle.py --jeu loto --export-web docs/loto.json --systeme 9
python3 oracle.py --jeu euromillions --export-web docs/euromillions.json
cd docs && python3 -m http.server 8000     # → http://localhost:8000
```

Le moteur télécharge l'historique FDJ tout seul : URL directe → auto-découverte
sur la page FDJ (auto-réparation si les URLs changent) → ton mirror GitHub.
Secours manuel : télécharger le ZIP sur fdj.fr et passer `--zip fichier.zip`.

## Déploiement auto-hébergé (10 minutes)

1. Nouveau repo GitHub avec cette structure (le dossier du ZIP est prêt :
   `git init && git add -A && git commit && git push`).
2. Settings → Pages → Deploy from branch → `main` `/docs` → ta page est en ligne.
3. Onglet Actions → « Pronostics Oracle » → Run workflow (1er lancement).
   Ensuite il tourne tout seul chaque matin : télécharge, recalcule
   (calibration comprise), commit les JSON + les CSV mirrors.

## Intégration dans ton app (Next.js)

```js
const r = await fetch(
  "https://raw.githubusercontent.com/TON_USER/TON_REPO/main/docs/loto.json",
  { cache: "no-store" });          // CORS ouvert sur raw.githubusercontent
const data = await r.json();
```
Ou copie `docs/` dans `/public/oracle/` de ton Next.js. Le portage en
composant React est la phase 5 du MASTERPROMPT.

## Le grand livre (nouveau)

Chaque exécution **verrouille** les grilles annoncées pour le prochain tirage
(première annonce = définitive — rien n'est jamais réécrit). Dès que le tirage
réel apparaît dans les données, chaque grille annoncée est **réglée en euros
avec les vrais rapports FDJ** : bons numéros, rang obtenu, gain exact. Le
cumul misé / gagné / ROI par mode s'affiche sur la page et se construit tout
seul, tirage après tirage, via le cron (le fichier `data/historique_*.json`
est commité). `--simulation 150` ajoute en plus une rétro-simulation en euros
sur les 150 derniers tirages. C'est le compteur d'honnêteté : il convergera
vers −(1−TRJ) ≈ −46 %, et il est là pour ça.

## Options du moteur

```
--jeu loto|euromillions      --mode hybride|pronostic|anti     --grilles N
--jackpot 17000000           --systeme 9        (pool 7-12, garantie ≥3 si 5)
--zip a.zip | --csv f.csv    --mirror URL       --save-csv data/x.csv
--export-web docs/x.json     --seed N           --no-backtest
--simulation [N]             (rétro-simulation € sur N tirages, déf. 150)
--aujourdhui AAAA-MM-JJ      (force la date de référence — tests/rejeu)
```

## Ce qui est réel, ce qui ne l'est pas

- **Réel** : la calibration (régression sur les colonnes gagnants — validée :
  corr 0,91 avec la popularité injectée en test), l'EV en euros avec partage
  attendu, les garanties combinatoires des systèmes, le test χ².
- **Pas réel** : tout pouvoir prédictif. P(jackpot) = 1/19 068 840 (Loto) et
  1/139 838 160 (EuroMillions) pour toute grille. Le backtest walk-forward
  tourne à chaque mise à jour pour le prouver.

Jouer comporte des risques : 09 74 75 13 13 · joueurs-info-service.fr
