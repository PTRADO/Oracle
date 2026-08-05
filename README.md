# 🎱 ORACLE v2.3 — Loto + EuroMillions

Une grille par tirage, et le coût réel affiché sans fard.

Le moteur tourne sur les données réelles FDJ, en concaténant toutes les
époques d'archives compatibles avec la formule actuelle du jeu : 1473 tirages
Loto (depuis mars 2017) et 1029 EuroMillions (depuis septembre 2016). Zéro
dépendance, stdlib pure.

La page ne propose **qu'une seule grille** — celle qui évite les numéros les
plus joués en France. Tout le reste (pourquoi ces numéros, est-ce que ça
marche, plusieurs tickets, provenance des données) vit dans des sections
repliées.

## Structure

```
oracle.py                         # moteur bi-jeux (Python stdlib)
recherche.py                      # recherche de formule + modèle nul
docs/index.html                   # la mini-app (1 fichier, vanilla)
docs/loto.json                    # pronostics Loto (généré)
docs/euromillions.json            # pronostics EuroMillions (généré)
data/                             # archives FDJ (versionnées = mirror)
tests/                            # pytest + fixtures d'archives réelles
.github/workflows/pronostics.yml  # cron quotidien 04h30 UTC
.github/workflows/ci.yml          # lint ruff + tests, hors ligne
```

## Démarrage local (1 minute)

```bash
python3 oracle.py --jeu loto --export-web docs/loto.json --systeme 9
python3 oracle.py --jeu euromillions --export-web docs/euromillions.json
cd docs && python3 -m http.server 8000     # → http://localhost:8000
```

Le moteur télécharge seul **toutes les époques** de l'historique FDJ : URL
directe → auto-découverte sur la page FDJ (auto-réparation si les
identifiants changent) → mirror GitHub du dépôt → cache local. Une époque close ne
bouge plus jamais : elle n'est téléchargée qu'une fois.
Secours manuel : télécharger un ZIP sur fdj.fr et passer `--zip fichier.zip`.

### Quelles archives, et pourquoi celles-là

On ne concatène que les époques dont la **structure de gains est identique**
à celle codée dans `rang_gagne` — sinon le grand livre réglerait les grilles
avec la mauvaise table de rangs. Sont donc écartés, volontairement et
explicitement (`meta.epoques_exclues`) :

| Jeu | Époques retenues | Écartées |
|---|---|---|
| Loto | `loto_2017`, `loto_201902`, `loto_201911` | Loto 6/49 d'avant 2008, formule à 6 rangs de 2008-2017, Super Loto / Grand Loto / Loto de Noël |
| EuroMillions | `euromillions_4`, `euromillions_201902`, `euromillions_202002` | époques à 9 et 11 étoiles (avant sept. 2016) |

### Contrôle d'intégrité

Chaque chargement audite les données et publie ses constats dans
`meta.alertes[]`, affichés tels quels sur la page : époque manquante, date en
double, trou de plus de 7 jours, tirage un jour inhabituel (= tirage spécial
infiltré), bonus jamais tiré (= ancienne formule mêlée), historique en
retard. Le cron **échoue** si une alerte critique sort. Une donnée douteuse
se voit ; elle ne s'absorbe pas en silence.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install pytest ruff
.venv/bin/python -m pytest tests/ -q     # 106 tests, hors ligne
.venv/bin/ruff check .
```

Les tests s'appuient sur des **extraits réels** d'archives FDJ (une par époque
de format, octets d'origine préservés pour éprouver aussi le décodage utf-8 /
latin-1). Ils couvrent le parser, le contrôle d'intégrité, la table des rangs
officiels, le chercheur de formule et le contrat JSON — garde-fous produit
compris : présence du backtest, EV négative, grand livre non masqué, et
absence de conclusion à un signal.

## Déploiement auto-hébergé (10 minutes)

1. Cloner ce dépôt (ou le forker).
2. Settings → Actions → General → Workflow permissions → **Read and write** —
   sans quoi le cron ne pourra pas committer ses résultats.
3. Settings → Pages → Deploy from branch → `main` `/docs` → la page est en ligne.
4. Onglet Actions → « Pronostics Oracle » → Run workflow (1er lancement).
   Ensuite il tourne tout seul chaque matin : télécharge, recalcule
   (calibration comprise), commit les JSON et les archives.

## Consommer les exports depuis une autre application

Les deux JSON sont servis avec un CORS ouvert : n'importe quelle page peut
les lire directement.

```js
const r = await fetch(
  "https://raw.githubusercontent.com/UTILISATEUR/DEPOT/main/docs/loto.json",
  { cache: "no-store" });
const data = await r.json();
```

Le contrat de ces fichiers est documenté par `tests/test_contrat_json.py` et
versionné par `meta.version` : tout changement cassant impose un bump.

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
--zip a.zip | --csv f.csv    (source unique, court-circuite le multi-époques)
--mirror URL                 (gabarit {label} → .../data/{label}.zip)
--save-csv data/x.csv        (avec --zip/--csv seulement)
--export-web docs/x.json     --seed N           --no-backtest
--simulation [N]             (rétro-simulation € sur N tirages, déf. 150)
--recherche [BUDGET]         (recherche de formule + 12 témoins, déf. 400)
--aujourdhui AAAA-MM-JJ      (force la date de référence — tests/rejeu)
```

## Ce qui est réel, ce qui ne l'est pas

- **Réel** : la calibration (régression sur les colonnes gagnants), l'EV en
  euros avec partage attendu, les garanties combinatoires des systèmes, le
  test χ².
- **Pas réel** : tout pouvoir prédictif. P(jackpot) = 1/19 068 840 (Loto) et
  1/139 838 160 (EuroMillions) pour toute grille. Le backtest walk-forward
  tourne à chaque mise à jour pour le prouver.

### On a cherché LA formule. Voici ce que ça donne.

`recherche.py` explore 400 combinaisons de 14 indicateurs (fréquences sur 3
fenêtres, retards bruts et relatifs, moyennes mobiles à 3 demi-vies,
momentum, Markov, paires, jour, voisins, tendance), puis affine par ascension
de coordonnées depuis les 5 meilleurs points.

Le résultat n'a de valeur que grâce à trois garde-fous :

1. **Validation hors échantillon** — cherché sur les 70 % les plus anciens,
   jugé sur les 30 % restants, jamais vus.
2. **Témoin par permutation** — la recherche entière est relancée 12 fois sur
   les mêmes tirages dans le désordre. Toute structure est détruite ; ce qui
   reste mesure ce que la recherche fabrique toute seule.
3. **Validation du chercheur** — sur une urne truquée, il doit retrouver le
   biais. Il double effectivement le score hors échantillon (test
   `test_chercheur_retrouve_un_signal_plante`). Sans cette preuve, un
   résultat négatif ne vaudrait rien.

| bons numéros / tirage | Loto | EuroMillions |
|---|---|---|
| Meilleure formule, sur le passé connu | 0,6168 | 0,6077 |
| **La même, sur l'inconnu** | **0,5165** | **0,5395** |
| Témoin (données mélangées), sur l'inconnu | 0,5104 | 0,5023 |
| Meilleur témoin | 0,5660 | 0,5636 |
| Pur hasard | 0,5102 | 0,5000 |
| p empirique | 0,46 | 0,15 |

Lecture : la formule gagne ~20 % sur les données qu'elle a vues, puis retombe
sur le hasard dès qu'on la confronte à l'inconnu. Le témoin fait exactement
pareil. Sur EuroMillions, **le meilleur témoin sur du bruit pur (0,5636) bat
même le résultat réel (0,5395)**.

Chercher 60 fois plus fort ne change rien : l'illusion d'entraînement monte
(+0,085 → +0,105), le gain réel reste collé à zéro (−0,029 · +0,011 · −0,001).

### Ce que les données réelles ont dit (04/08/2026)

| | Loto (1473 tirages) | EuroMillions (1029 tirages) |
|---|---|---|
| Backtest walk-forward | modèle 0,5209 · hasard 0,5053 · théorique 0,5102 → **écart dans le bruit** | modèle 0,5036 · hasard 0,4974 · théorique 0,5000 → **écart dans le bruit** |
| Sur-joués mesurés (β) | 13, 12, 11, 7, 3, 5 | 7, 12, 13, 25, 6, 11 |
| Délaissés mesurés (β) | 37, 41, 47, 40, 38, 46 | 41, 46, 32, 35, 34, 39 |
| Effet anniversaire | r = +0,189 | r = +0,245 |
| χ² biais physique | 35,3 / seuil 65,2 → rien | 46,6 / seuil 66,3 → rien |

Lecture : le folklore ne bat pas le hasard, **et les données le disent
elles-mêmes**. En revanche l'effet de partage est bien là — les numéros
sur-joués sont massivement des dates d'anniversaire (≤ 31), les délaissés des
numéros hauts. C'est le seul levier que ce produit exploite, et il agit sur
le **montant** d'un gain éventuel, jamais sur sa probabilité.

Jouer comporte des risques : 09 74 75 13 13 · joueurs-info-service.fr
