# 🎱 ORACLE v2.8 — Loto + EuroMillions

Une grille par tirage, et le coût réel affiché sans fard.

Le moteur tourne sur les données réelles FDJ, en concaténant toutes les
époques d'archives compatibles avec la formule actuelle du jeu : 1473 tirages
Loto (depuis mars 2017) et 1029 EuroMillions (depuis septembre 2016). Zéro
dépendance, stdlib pure.

La page ne propose **qu'une seule grille** — celle qui évite les numéros les
plus joués en France — et un regard sur l'historique : chaque tirage rejoué,
avec les vraies boules FDJ sorties et le verdict du ticket (TAG « Perdant »
ou gain brut en euros). Chaque grille affichée est aussi réglée contre le
dernier tirage réel. Les explications de méthode vivent ici, dans le dépôt,
pas sur la page.

## Structure

```
oracle.py                         # moteur bi-jeux (Python stdlib)
recherche.py                      # recherche de formule + modèle nul
docs/index.html                   # la mini-app (1 fichier, vanilla)
docs/loto.json                    # pronostics Loto (généré)
docs/euromillions.json            # pronostics EuroMillions (généré)
docs/favicon.svg                  # la marque (source vectorielle)
docs/*.png, favicon.ico           # icônes générées par tools/icones.py
tools/icones.py                   # fabrique d'icônes (stdlib, à la demande)
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
.venv/bin/python -m pytest tests/ -q     # ~290 tests, hors ligne
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
--partage [N]                (backtest apparié du partage, 0 = tout l'historique)
--aujourdhui AAAA-MM-JJ      (force la date de référence — tests/rejeu)
```

## Le partage joue à tous les rangs (v2.8)

Jusqu'en v2.7 le moteur ne corrigeait le partage qu'au **rang 1**. Erreur de
cadrage, pas de calcul : dans un jeu de tirage français, *tous* les rangs sont
pari-mutuels — une part fixe de la cagnotte divisée par le nombre de gagnants.
Le produit annonçait donc son unique levier réel sous la seule forme
invérifiable qui soit : un gain réalisable une fois tous les 122 000 ans.

L'élasticité du rapport à la popularité de la combinaison sortie est mesurée
rang par rang, sur les rapports FDJ réels, en contrôlant l'affluence par un
rang insensible aux boules :

| Loto — rang | m | β | t | part de l'EV |
|---|---|---|---|---|
| 2 · 5 numéros | 5 | −0,602 | −16,7 | 5,2 % |
| 4 · 4 numéros | 4 | −0,666 | −67,7 | 5,8 % |
| 6 · 3 numéros | 3 | −0,465 | −86,8 | 11,5 % |
| 8 · 2 numéros | 2 | −0,264 | −58,9 | **36,6 %** |
| 9 · Chance seule | 0,38 | *prix fixe* | — | 26,3 % |

### Trois pièges, trouvés par audit adverse et corrigés

**1. Le régime de gains.** Avant le 4 novembre 2019, les rangs 2 à 9 du Loto
payaient un montant **fixe** : sur les 417 tirages antérieurs, chaque rang ne
prend qu'*une seule valeur distincte*. Ces tirages ne peuvent porter aucune
élasticité — les inclure ajoutait 28 % d'observations à pente nulle par
construction, et rabotait toutes les pentes d'autant. `debut_pari_mutuel` les
écarte, rang par rang, sans date codée en dur : il détecte une plage de 30
rapports rigoureusement identiques, ce qu'aucun régime pari-mutuel ne produit.
C'est le même piège que « facturer le passé au tarif d'aujourd'hui », déjà
corrigé deux fois dans ce moteur.

**2. β ne s'applique pas tel quel.** β répond à la popularité du *tirage
sorti*, pas à celle de ta grille. Quand tu touches m bons numéros, la
combinaison sortie contient m de tes numéros — et `pick − m` numéros tirés du
**complémentaire** de ta grille, dont la popularité est l'*opposée* de la
tienne (γ est centré sur l'univers). La charge exacte vaut donc

```
m/pick − (pick − m)/(n_max − pick)
```

et non `m/pick`. L'écart atteint un facteur 1,83 au rang à 1 bon numéro. Le
piège avait tenu parce que le terme oublié **s'annule identiquement en
m = pick** : le contrôle « à 5 bons numéros, la combinaison sortie est ta
grille » ne pouvait rien détecter. Il est remplacé par une vérification
Monte-Carlo à tous les m. Vérification du correctif : à m = 1, popularité
moyenne mesurée −0,1076, formule exacte −0,1073, ancienne formule −0,1967.

**3. Le placebo ne pouvait pas échouer.** Il prenait le rang à m ≈ 0 et
vérifiait qu'il sortait à zéro. Au Loto ce rang paie 2,20 € — une seule valeur
distincte sur 1474 tirages. Son β était nul par identité comptable : une
tautologie publiée comme une réfutation. Le placebo est désormais une
**permutation** : la mesure entière rejouée sur des tirages dont on a mélangé
les combinaisons. Loto 4 coefficients significatifs sur 48 (8 %, contre 5 %
attendus), |t| max 2,55 ; EuroMillions 0 sur 78, |t| max 1,95 — face à des
|t| réels allant jusqu'à 87.

### Ce que cette section prouve, et ce qu'elle ne prouve pas

À dire avant les résultats. Au **Loto**, le rapport est l'inverse quasi exact
du nombre de gagnants : régressés sur les mêmes variables, β_rapport / β_gagnants
vaut 0,95 à 0,98. Or le nombre de gagnants est précisément ce que la
calibration ajuste. Cette section ne fournit donc pas, au Loto, une
confirmation *indépendante* : elle traduit la calibration en euros, rang par
rang. C'est son objet — ce n'est pas une seconde preuve.

À l'**EuroMillions** le ratio tombe à 0,68–0,82, et pour une raison connue :
les gagnants publiés sont *français* alors que le rapport est fixé par le pool
*européen*. Il y a là un contenu que la calibration ne peut pas voir.

La preuve indépendante, elle, est le backtest — qui ne touche jamais à ce
modèle.

### Le backtest apparié

`--partage` rejoue l'historique en walk-forward strict et mesure **le rapport
encaissé sachant qu'un rang a été touché** — pas le ROI, dont ce dépôt a déjà
montré qu'il est dominé par la chance. La probabilité de toucher ne dépend
d'aucune stratégie ; le montant, si.

| Stratégie | Loto (600 tirages) | EuroMillions (600) |
|---|---|---|
| **grille la moins jouée** | **+14,5 %** | **+9,4 %** |
| les mêmes boules, bonus au hasard | +10,6 % | +2,1 % |
| grille au hasard (témoin) | −1,2 % | +0,3 % |
| 5 numéros ≤ 31 « anniversaire » | −9,2 % | −4,7 % |
| grille la plus jouée | −14,9 % | −16,1 % |

Une observation par **tirage**, pas par grille : deux grilles qui touchent le
même rang le même soir encaissent le même rapport et n'apportent pas deux
informations. Les rangs touchés moins de 5 fois sont exclus du tableau *et* du
total — sans ce filtre, un rang touché deux fois portait 30 % du chiffre de
tête, invisible.

Au Loto, la grille délaissée touche un rang **11,6 fois par an**, contre une
fois tous les 122 000 ans pour le jackpot. C'est toute la différence : le
levier devient mesurable dans une vie humaine.

Modèle prudent +13,9 % (Loto) et +10,9 % (Euro) ; mesure +14,5 % et +9,4 %.
Les deux se rejoignent, et le chiffre publié reste le prudent.

### Le n° Chance et les Étoiles sont un levier séparé

Découvert en écrivant le placebo, et pas avant : le rang « 1 + 2 étoiles »
bougeait de −40 % pour la stratégie « populaire », alors qu'il ne demande
qu'*un* bon numéro. La cause n'était pas les boules mais les **étoiles**. Le
backtest sépare donc les deux leviers — `anti_boules` joue les mêmes boules
avec un bonus tiré au sort — et le placebo n'est vérifié que sur les stratégies
à bonus neutre. Le bonus vaut à lui seul **+3,9 points** au Loto et
**+7,3 points** à l'EuroMillions.

### Ce que ça vaut, en euros

**+4,94 %** de la mise au Loto, **+1,79 %** à l'EuroMillions. L'espérance passe
de −51,0 % à −46,1 %.

| Mode | partage | valeur mesurée |
|---|---|---|
| `anti` | ×0,22 | **+4,94 % de la mise** |
| `hybride` | ×0,27 | +4,76 % |
| `pronostic` | ×1,32 | **−1,30 %** |

Le mode `pronostic` joue les numéros que tout le monde joue : il partage
davantage, donc il **détruit** de la valeur. Il est conservé pour le fun, et
son prix est désormais affiché — un produit qui propose un mode sans dire ce
qu'il coûte n'est pas honnête. La page, elle, n'a jamais publié que l'`anti`.

### Le cas où l'espérance devient positive — et pourquoi ce n'est pas un signal

Elle reste négative à tout jackpot ordinaire. Mais l'honnêteté impose de dire
ceci : au-delà d'environ **26 M€** de report au Loto, l'arithmétique donne une
espérance *positive* pour une grille délaissée. Le moteur ne le cache pas — et
ne le publie jamais nu. Toute EV positive sort accompagnée d'un avertissement,
pour deux raisons qui la vident de sa portée pratique :

1. `n_est` est la participation **médiane** des 160 derniers tirages. Or les
   soirs de gros jackpot sont exactement ceux où la foule explose, donc où les
   partageurs se multiplient. Le chiffre est un **majorant**, pas une
   prévision — le corriger demanderait un historique des jackpots que ce dépôt
   n'a pas.
2. Même exacte, cette espérance reposerait à 100 % sur un événement à 1 sur
   19 068 840. L'espérance monte ; le résultat le plus probable reste de tout
   perdre.

Aucun raffinement de ce moteur ne changera la première ligne : c'est un jeu.

## Ce qui est réel, ce qui ne l'est pas

- **Réel** : la calibration anti-partage, l'EV en euros avec partage attendu
  **à tous les rangs** (v2.8), les garanties combinatoires des systèmes, le
  test χ².

  La calibration mérite un mot, c'est le seul levier du produit. La FDJ publie
  les gagnants **rang par rang**. Tous les rangs d'un même tirage ont vu la
  même foule, mais pas le même nombre de numéros trouvés : en les comparant
  entre eux, l'affluence du soir s'annule d'elle-même — jackpot, saison,
  publicité — et il ne reste que l'effet des numéros. C'est un panel à effets
  fixes de tirage (v2.4 ; la v2.3 régressait le total des gagnants, dont 55 %
  au Loto ne dépendent pas des 5 boules).

  Ce qui rend la mesure légitime : **c'est la FDJ qui tire les numéros au
  sort**. La variable explicative est randomisée, donc aucun facteur caché ne
  peut expliquer le résultat. C'est une expérience randomisée dont la FDJ
  publie les résultats, pas une corrélation trouvée après coup.

  Vérifiée hors échantillon sur le rang à 5 bons numéros — que la calibration
  n'utilise jamais, il est trop creux — avec une prédiction chiffrée : le
  modèle annonce un coefficient de 0,923 (Loto), on mesure 0,966 ± 0,045.
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

Depuis la v2.8, ce montant est corrigé à **tous les rangs** et non au seul
jackpot — ce qui rend le levier vérifiable une douzaine de fois par an au lieu
d'une fois tous les 122 000 ans. `--partage` le mesure sans passer par le
modèle, et `tests/test_elasticite.py` le réfuterait s'il n'était pas là.

Jouer comporte des risques : 09 74 75 13 13 · joueurs-info-service.fr
