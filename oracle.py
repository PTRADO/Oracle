#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 ORACLE v2.0 MAX — Loto + EuroMillions
 Pronostiqueur multi-techniques · Calibration empirique · EV jackpot · Systèmes
================================================================================

Un seul fichier, zéro dépendance (stdlib pure). Python 3.10+.

NOUVEAUTÉS v2 (vs v1.1)
-----------------------
• BI-JEUX : --jeu loto (5/49 + Chance/10) ou --jeu euromillions (5/50 + 2
  Étoiles/12). Téléchargement auto avec auto-découverte des liens FDJ.
• CALIBRATION EMPIRIQUE (le vrai upgrade) : régression ridge sur les colonnes
  "nombre de gagnants" de l'historique FDJ pour APPRENDRE la popularité réelle
  de chaque numéro en France — remplace les heuristiques par des poids mesurés.
  Clé mathématique : les numéros tirés sont aléatoires, donc décorrélés de la
  participation → les coefficients sont identifiés sans biais malgré le bruit.
• EV JACKPOT : espérance de gain en euros par grille = gains hors-jackpot
  mesurés dans l'historique (rapports × gagnants / participation estimée)
  + composante jackpot corrigée du PARTAGE attendu selon la popularité
  calibrée de TA grille. Jackpot scrapé sur fdj.fr ou fourni via --jackpot.
  Les paramètres EV sont exportés → la page web recalcule l'EV en direct
  quand tu changes le montant du jackpot.
• SYSTÈMES RÉDUCTEURS (--systeme N) : génération par couverture gloutonne
  d'un jeu de grilles sur tes N meilleurs numéros avec GARANTIE COMBINATOIRE
  VÉRIFIÉE par le code : si les 5 numéros sortants sont dans ton pool,
  au moins une grille contient ≥ 3 bons numéros.
• Le socle v1 reste : 8 techniques folklore documentées, anti-partage,
  backtest walk-forward, test d'effet anniversaire, test χ² de biais physique.

USAGE
-----
    python3 oracle.py                                   # loto, hybride
    python3 oracle.py --jeu euromillions                # euromillions
    python3 oracle.py --jackpot 17000000                # force le jackpot (€)
    python3 oracle.py --systeme 9                       # système réducteur
    python3 oracle.py --export-web docs/loto.json       # JSON pour la page
    python3 oracle.py --zip archive.zip  --csv fich.csv # sources locales
    python3 oracle.py --mirror URL --save-csv data/x.csv

HONNÊTETÉ (assumée, vérifiée en continu)
----------------------------------------
P(jackpot) : 1/19 068 840 (Loto) · 1/139 838 160 (EuroMillions), pour toute
grille. T1-T8 = folklore (le backtest le démontre à chaque exécution). Les
seuls leviers réels : anti-partage calibré (montant, pas probabilité), EV
(quand jouer est le moins mauvais), χ² (surveillance du seul edge physique
théorique). L'EV reste négative : c'est un jeu, pas un investissement.
================================================================================
"""

from __future__ import annotations

import argparse
import csv
import io
import itertools
import json
import math
import os
import random
import re
import statistics
import sys
import urllib.request
import zipfile
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta

# ==============================================================================
# 0. CONFIGURATION DES JEUX
# ==============================================================================

JEUX = {
    "loto": {
        "nom": "LOTO",
        "n_max": 49, "pick": 5,
        "bonus_max": 10, "bonus_pick": 1, "bonus_nom": "chance",
        "bonus_patterns": [r"numero[_ ]?chance"],
        "prix": 2.20,
        "p_any_win": 1 / 6,          # "1 chance sur 6 de gagner" (FDJ)
        "jours": {0, 2, 5},          # lundi, mercredi, samedi
        "page_hist": "https://www.fdj.fr/jeux-de-tirage/loto/historique",
        "page_jeu": "https://www.fdj.fr/jeux-de-tirage/loto",
        "url_directe": ("https://www.sto.api.fdj.fr/anonymous/service-draw-info/"
                        "v3/documentations/1a2b3c4d-9876-4562-b3fc-2c963f66afp6"),
        "pref_lien": "novembre 2019",
        "titre_lien": r"historique\s+loto",
    },
    "euromillions": {
        "nom": "EUROMILLIONS",
        "n_max": 50, "pick": 5,
        "bonus_max": 12, "bonus_pick": 2, "bonus_nom": "étoiles",
        "bonus_patterns": [r"etoile[_ ]?1", r"etoile[_ ]?2"],
        "prix": 2.50,
        "p_any_win": 1 / 13,         # ~1 chance sur 13 de gagner
        "jours": {1, 4},             # mardi, vendredi
        "page_hist": ("https://www.fdj.fr/jeux-de-tirage/"
                      "euromillions-my-million/historique"),
        "page_jeu": "https://www.fdj.fr/jeux-de-tirage/euromillions-my-million",
        "url_directe": None,          # résolu par auto-découverte
        "pref_lien": "2020",
        "titre_lien": r"historique\s+euro\s*millions",
    },
}

JOURS_FR = {0: "lundi", 1: "mardi", 2: "mercredi", 3: "jeudi",
            4: "vendredi", 5: "samedi", 6: "dimanche"}

POIDS_FOLKLORE = {"frequence": 1.0, "retard": 1.0, "ewma": 1.0,
                  "momentum": 1.0, "markov": 1.0, "paires": 1.0, "jour": 0.5}

BAR_FULL, BAR_EMPTY = "█", "░"


def proba_jackpot(cfg) -> int:
    return (math.comb(cfg["n_max"], cfg["pick"])
            * math.comb(cfg["bonus_max"], cfg["bonus_pick"]))


def nums(cfg):
    return range(1, cfg["n_max"] + 1)


def bonus_nums(cfg):
    return range(1, cfg["bonus_max"] + 1)


# ==============================================================================
# 1. RÉSEAU + CHARGEMENT DES DONNÉES
# ==============================================================================

def _http_get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Oracle/2.0",
        "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def decouvrir_liens_fdj(cfg) -> list[str]:
    """Scrape la page d'historique FDJ du jeu et extrait les liens ZIP.
    Auto-réparation : survit aux changements d'URLs FDJ tant que la page
    liste ses archives. Priorité à l'archive de la formule actuelle."""
    try:
        html = _http_get(cfg["page_hist"]).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"    (scrape page FDJ impossible : {type(e).__name__})")
        return []
    liens = re.findall(
        r'href="(https?://[^"]+?)"[^>]*title="([^"]*)"', html)
    utiles = [(u, t) for u, t in liens
              if re.search(cfg["titre_lien"], t, re.I)
              or ("sto.api.fdj.fr" in u and "documentations" in u)]
    prio = [u for u, t in utiles if cfg["pref_lien"] in t.lower()]
    autres = [u for u, t in utiles if u not in prio]
    return list(dict.fromkeys(prio + autres))[:4]


def telecharger_archive(cfg, dest_dir: str, mirror: str | None) -> str | None:
    """Ordre : URL directe connue → auto-découverte → mirror perso."""
    os.makedirs(dest_dir, exist_ok=True)
    candidates = [u for u in [cfg["url_directe"]] if u]
    print("  → auto-découverte des liens sur la page FDJ…")
    candidates += [u for u in decouvrir_liens_fdj(cfg) if u not in candidates]
    if mirror:
        candidates.append(mirror)
    for url in candidates:
        try:
            print(f"  → tentative : {url[:76]}…" if len(url) > 78 else
                  f"  → tentative : {url}")
            data = _http_get(url)
            if len(data) < 500:
                print("    ✗ réponse trop courte, ignorée")
                continue
            ext = ".zip" if data[:2] == b"PK" else ".csv"
            path = os.path.join(dest_dir, f"{cfg['nom'].lower()}_fdj{ext}")
            with open(path, "wb") as f:
                f.write(data)
            print(f"  ✔ historique récupéré ({len(data)//1024} Ko, {ext[1:]})")
            return path
        except Exception as e:  # noqa: BLE001
            print(f"    ✗ échec ({type(e).__name__})")
    return None


def scraper_jackpot(cfg) -> float | None:
    """Scrape le montant du jackpot du prochain tirage sur la page du jeu."""
    try:
        html = _http_get(cfg["page_jeu"]).decode("utf-8", errors="replace")
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*millions", html, re.I)
        if m:
            return float(m.group(1).replace(",", ".")) * 1_000_000
    except Exception:  # noqa: BLE001
        pass
    return None


def _decoder(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _sniff_delim(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in (";", ",", "\t")}
    return max(counts, key=counts.get)


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y%m%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_int(s: str) -> int | None:
    s = re.sub(r"[^\d-]", "", s or "")
    try:
        return int(s) if s and s != "-" else None
    except ValueError:
        return None


def _to_float(s: str) -> float | None:
    s = (s or "").strip().replace("\u202f", "").replace(" ", "")
    s = s.replace(",", ".")
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def parser_csv(cfg, texte: str) -> list[dict]:
    """Parse un CSV FDJ (Loto ou EuroMillions), tolérant aux variantes.
    Retourne des tirages triés par date : {date, jour, balls, bonus,
    gagnants{rang:int}, rapports{rang:float}}."""
    lignes = [l for l in texte.splitlines() if l.strip()]
    if not lignes:
        return []
    delim = _sniff_delim(lignes[0])
    reader = csv.reader(io.StringIO(texte), delimiter=delim)
    header = [h.strip().lower() for h in next(reader)]

    def find_col(pattern: str) -> int | None:
        for i, h in enumerate(header):
            if re.search(pattern, h) and "second" not in h:
                return i
        return None

    col_date = find_col(r"date_de_tirage|date.?tirage")
    cols_boules = [find_col(rf"boule[_ ]?{k}\b") for k in range(1, cfg["pick"] + 1)]
    cols_bonus = [find_col(p) for p in cfg["bonus_patterns"]]

    def rang_de(h: str) -> int | None:
        m = re.search(r"rang\s*_?(\d+)", h)
        return int(m.group(1)) if m else None

    cols_gagnants, cols_rapports = {}, {}
    for i, h in enumerate(header):
        if "second" in h or "my_million" in h or "etoile+" in h:
            continue
        r = rang_de(h)
        if r is None:
            continue
        if "nombre_de_gagnant" in h and r not in cols_gagnants:
            cols_gagnants[r] = i
        elif "rapport" in h and r not in cols_rapports:
            cols_rapports[r] = i

    if col_date is None or any(c is None for c in cols_boules):
        raise ValueError(
            f"Colonnes introuvables dans le CSV {cfg['nom']} "
            "(attendu : date_de_tirage, boule_1..boule_5"
            + (", numero_chance" if cfg["bonus_pick"] == 1
               else ", etoile_1, etoile_2") + ").")

    tirages = []
    for row in reader:
        if len(row) < len(header) - 2:
            continue
        d = _parse_date(row[col_date])
        if d is None:
            continue
        balls, ok = [], True
        for c in cols_boules:
            v = _to_int(row[c]) if c is not None and c < len(row) else None
            if v is None or not (1 <= v <= cfg["n_max"]):
                ok = False
                break
            balls.append(v)
        if not ok or len(set(balls)) != cfg["pick"]:
            continue
        bonus = []
        for c in cols_bonus:
            if c is not None and c < len(row):
                v = _to_int(row[c])
                if v is not None and 1 <= v <= cfg["bonus_max"]:
                    bonus.append(v)
        gagnants, rapports = {}, {}
        for r, i in cols_gagnants.items():
            if i < len(row):
                v = _to_int(row[i])
                if v is not None:
                    gagnants[r] = v
        for r, i in cols_rapports.items():
            if i < len(row):
                v = _to_float(row[i])
                if v is not None:
                    rapports[r] = v
        tirages.append({"date": d, "jour": d.weekday(),
                        "balls": tuple(sorted(balls)),
                        "bonus": tuple(sorted(bonus)),
                        "gagnants": gagnants, "rapports": rapports})
    tirages.sort(key=lambda t: t["date"])
    return tirages


def charger_tirages(cfg, args) -> list[dict]:
    texte = None
    if args.csv:
        with open(args.csv, "rb") as f:
            texte = _decoder(f.read())
    else:
        zpath = args.zip
        if not zpath:
            print(f"Téléchargement de l'historique {cfg['nom']}…")
            zpath = telecharger_archive(
                cfg, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "data"),
                mirror=getattr(args, "mirror", None))
        if not zpath:
            print(f"\n✗ Téléchargement impossible.\n"
                  f"  Télécharge le ZIP à la main : {cfg['page_hist']}\n"
                  f"  puis relance avec --zip fichier.zip\n")
            sys.exit(1)
        with open(zpath, "rb") as f:
            brut = f.read()
        if brut[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(brut)) as z:
                noms = [n for n in z.namelist() if n.lower().endswith(".csv")]
                if not noms:
                    print("✗ Aucun CSV dans l'archive.")
                    sys.exit(1)
                nom = max(noms, key=lambda n: z.getinfo(n).file_size)
                texte = _decoder(z.read(nom))
        else:
            texte = _decoder(brut)
    tirages = parser_csv(cfg, texte)
    if getattr(args, "save_csv", None):
        os.makedirs(os.path.dirname(os.path.abspath(args.save_csv)),
                    exist_ok=True)
        with open(args.save_csv, "w", encoding="utf-8") as f:
            f.write(texte)
        print(f"  💾 CSV mirror sauvegardé : {args.save_csv}")
    if len(tirages) < 60:
        print(f"✗ Seulement {len(tirages)} tirages exploitables — trop peu.")
        sys.exit(1)
    return tirages


# ==============================================================================
# 2. NORMALISATION
# ==============================================================================

def normaliser(scores: dict[int, float]) -> dict[int, float]:
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    if math.isclose(hi, lo):
        return {k: 50.0 for k in scores}
    return {k: 100.0 * (v - lo) / (hi - lo) for k, v in scores.items()}


def combiner(couches, poids) -> dict[int, float]:
    total_p = sum(poids.get(n, 0) for n in couches) or 1.0
    out = {}
    for k in next(iter(couches.values())):
        out[k] = sum(couches[n][k] * poids.get(n, 0) for n in couches) / total_p
    return out


# ==============================================================================
# 3. TECHNIQUES FOLKLORE T1-T8 (documentées ; statut : sophisme du joueur)
# ==============================================================================

def t1_frequence(cfg, tirages):
    """T1 — Numéros 'chauds'. Réalité : bruit d'échantillonnage pur."""
    c = Counter()
    for t in tirages:
        c.update(t["balls"])
    return {n: float(c.get(n, 0)) for n in nums(cfg)}


def t2_retard(cfg, tirages):
    """T2 — Numéros 'en retard'. LE sophisme du joueur : zéro mémoire."""
    dernier = {n: -1 for n in nums(cfg)}
    for i, t in enumerate(tirages):
        for b in t["balls"]:
            dernier[b] = i
    n_t = len(tirages)
    return {n: float(n_t - 1 - dernier[n]) if dernier[n] >= 0 else float(n_t)
            for n in nums(cfg)}


def t3_ewma(cfg, tirages, demi_vie: int = 30):
    """T3 — Chauds récents pondérés exponentiellement. Bruit lissé."""
    n_t = len(tirages)
    s = {n: 0.0 for n in nums(cfg)}
    for i, t in enumerate(tirages):
        w = 0.5 ** ((n_t - 1 - i) / demi_vie)
        for b in t["balls"]:
            s[b] += w
    return s


def t4_momentum(cfg, tirages, fenetre: int = 20):
    """T4 — Z-score de 'forme' sur 20 tirages. Encore plus bruité."""
    recents = tirages[-fenetre:]
    c = Counter()
    for t in recents:
        c.update(t["balls"])
    p = cfg["pick"] / cfg["n_max"]
    attendu = len(recents) * cfg["pick"] / cfg["n_max"]
    et = math.sqrt(len(recents) * p * (1 - p)) or 1.0
    return {n: (c.get(n, 0) - attendu) / et for n in nums(cfg)}


def t5_markov(cfg, tirages):
    """T5 — Transitions tirage N-1 → N. Converge vers l'uniforme (indép.)."""
    M = defaultdict(Counter)
    for prev, cur in zip(tirages, tirages[1:]):
        for i in prev["balls"]:
            M[i].update(cur["balls"])
    dernier = tirages[-1]["balls"]
    s = {n: 0.0 for n in nums(cfg)}
    for i in dernier:
        total = sum(M[i].values()) or 1
        for j in nums(cfg):
            s[j] += M[i].get(j, 0) / total
    return s


def t6_paires(cfg, tirages):
    """T6 — Affinités de paires. Queues de binomiale, décoratif."""
    pc = Counter()
    for t in tirages:
        b = t["balls"]
        for i in range(len(b)):
            for j in range(i + 1, len(b)):
                pc[(b[i], b[j])] += 1
    par_num = defaultdict(list)
    for (a, b), n in pc.items():
        par_num[a].append(n)
        par_num[b].append(n)
    return {n: float(sum(sorted(par_num.get(n, [0]), reverse=True)[:3]))
            for n in nums(cfg)}


def t7_jour(cfg, tirages, weekday: int):
    """T7 — 'Saisonnalité' du jour de tirage. Folklore intégral (demi-poids)."""
    c = Counter()
    for t in tirages:
        if t["jour"] == weekday:
            c.update(t["balls"])
    return {n: float(c.get(n, 0)) for n in nums(cfg)}


def distribution_deltas(cfg, tirages) -> Counter:
    """T8 — Delta system (appliqué en bonus de grille)."""
    dc = Counter()
    for t in tirages:
        b = t["balls"]
        dc.update([b[0]] + [b[k] - b[k - 1] for k in range(1, len(b))])
    return dc


# ==============================================================================
# 4. ANTI-PARTAGE — heuristique (T9a) puis CALIBRATION EMPIRIQUE (T9b)
# ==============================================================================

def _pop_heuristique(cfg, n: int) -> float:
    """Modèle de popularité issu de la littérature (anniversaires, fétiches).
    Sert de prior, remplacé/pondéré par la calibration empirique."""
    pop = 1.0
    if n <= 12:
        pop += 1.20
    elif n <= 31:
        pop += 0.70
    else:
        pop -= 0.15
    pop += {7: 0.90, 13: 0.35, 3: 0.30, 17: 0.25, 11: 0.20, 21: 0.15}.get(n, 0)
    if n >= 40:
        pop -= 0.10
    return pop


def _pop_bonus_heuristique(cfg, n: int) -> float:
    if cfg["bonus_pick"] == 1:      # Chance 1-10
        return {1: .75, 2: .60, 3: .80, 4: .55, 5: .65,
                6: .55, 7: 1.0, 8: .45, 9: .50, 10: .40}.get(n, .5)
    # Étoiles 1-12 : petits chiffres + 7 sur-joués
    return max(0.3, 1.25 - 0.06 * n) + (0.35 if n == 7 else 0)


# ---- T9b : CALIBRATION EMPIRIQUE ---------------------------------------------

def _resoudre(A, b):
    """Résout A·x = b par élimination de Gauss avec pivot partiel (stdlib)."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise ValueError("matrice singulière")
        M[col], M[piv] = M[piv], M[col]
        div = M[col][col]
        M[col] = [v / div for v in M[col]]
        for r in range(n):
            if r != col and M[r][col] != 0:
                f = M[r][col]
                M[r] = [a - f * c for a, c in zip(M[r], M[col])]
    return [M[i][n] for i in range(n)]


def calibration_empirique(cfg, tirages, lam: float = 4.0):
    """T9b — Apprend la popularité RÉELLE de chaque numéro depuis les données.

    Modèle : log(total_gagnants du tirage) = Σ β_n·x_n + contrôles + ε
      · x_n = 1 si le numéro n est sorti à ce tirage
      · contrôles : tendance temporelle (t, t²) + indicatrices de jour de
        tirage — absorbent l'essentiel des variations de participation.
      · Les gros écarts restants (jackpots exceptionnels → afflux de joueurs)
        sont du bruit NON CORRÉLÉ aux numéros tirés (le tirage est aléatoire),
        donc les β_n restent identifiés sans biais. C'est LA propriété qui
        rend cette régression légitime.
      · Ridge (λ) pour la stabilité ; β centrés (seuls les écarts relatifs
        de popularité ont un sens, Σx_n = pick est constant).

    Interprétation : β_n > 0 ⇒ quand n sort, il y a PLUS de gagnants ⇒ n est
    SUR-JOUÉ par les Français ⇒ à éviter pour ne pas partager. R² faible
    attendu (le signal popularité est petit vs le bruit participation) : ce
    qui compte est le CLASSEMENT des β, pas le R².
    """
    lignes = []
    jours = sorted(cfg["jours"])
    for i, t in enumerate(tirages):
        tot = sum(t["gagnants"].values())
        if tot <= 0:
            continue
        x = [0.0] * cfg["n_max"]
        for b in t["balls"]:
            x[b - 1] = 1.0
        tr = i / max(len(tirages) - 1, 1)
        ctrl = [tr, tr * tr] + [1.0 if t["jour"] == j else 0.0
                                for j in jours[1:]]
        lignes.append((x + ctrl + [1.0], math.log(tot)))
    if len(lignes) < 150:
        return None
    p = len(lignes[0][0])
    # Normal equations : (XᵀX + λI)β = Xᵀy   (λ=0 sur les contrôles/intercept)
    XtX = [[0.0] * p for _ in range(p)]
    Xty = [0.0] * p
    for x, y in lignes:
        for a in range(p):
            if x[a] == 0.0:
                continue
            Xty[a] += x[a] * y
            for b in range(a, p):
                XtX[a][b] += x[a] * x[b]
    for a in range(p):
        for b in range(a):
            XtX[a][b] = XtX[b][a]
    for a in range(cfg["n_max"]):
        XtX[a][a] += lam
    try:
        beta = _resoudre(XtX, Xty)
    except ValueError:
        return None
    # R²
    ys = [y for _, y in lignes]
    ybar = statistics.mean(ys)
    sse = sum((y - sum(bi * xi for bi, xi in zip(beta, x))) ** 2
              for x, y in lignes)
    sst = sum((y - ybar) ** 2 for y in ys) or 1.0
    r2 = 1 - sse / sst
    b_nums = beta[:cfg["n_max"]]
    moy = statistics.mean(b_nums)
    centres = {n: b_nums[n - 1] - moy for n in nums(cfg)}
    ordre = sorted(nums(cfg), key=lambda n: -centres[n])
    return {
        "beta": centres, "r2": round(r2, 4), "n_tirages": len(lignes),
        "top_surjoues": ordre[:6],       # β max = les plus joués en vrai
        "top_delaisses": ordre[-6:][::-1],
    }


def scores_anti(cfg, calib) -> tuple[dict[int, float], str]:
    """Score anti-partage final : 70% empirique (si calibré) + 30% heuristique."""
    heur = normaliser({n: -_pop_heuristique(cfg, n) for n in nums(cfg)})
    if not calib:
        return heur, "heuristique (littérature)"
    emp = normaliser({n: -calib["beta"][n] for n in nums(cfg)})
    mix = {n: 0.7 * emp[n] + 0.3 * heur[n] for n in nums(cfg)}
    return mix, f"empirique 70% (R²={calib['r2']}, n={calib['n_tirages']}) + heuristique 30%"


def pop_rel_grille(cfg, balls, calib) -> float:
    """Multiplicateur de popularité relative d'une grille (pour l'EV).
    exp(Σ β centrés) si calibré, sinon version heuristique log-centrée."""
    if calib:
        s = sum(calib["beta"][b] for b in balls)
    else:
        logs = {n: math.log(_pop_heuristique(cfg, n)) for n in nums(cfg)}
        m = statistics.mean(logs.values())
        s = sum(logs[b] - m for b in balls)
    return max(0.25, min(4.0, math.exp(s)))


def popularite_grille_penalite(balls) -> float:
    """Pénalités de motifs humains au niveau grille."""
    p = 0.0
    petits = sum(1 for b in balls if b <= 31)
    if petits >= 4:
        p += 1.5
    elif petits == 3:
        p += 0.5
    b = sorted(balls)
    if len({b[k] - b[k - 1] for k in range(1, len(b))}) == 1:
        p += 2.0
    if all(x <= 15 for x in b):
        p += 1.0
    return p


# ==============================================================================
# 5. SCORES BONUS (Chance / Étoiles)
# ==============================================================================

def bonus_scores(cfg, tirages):
    valides = [t for t in tirages if len(t["bonus"]) == cfg["bonus_pick"]]
    freq, dernier = Counter(), {n: -1 for n in bonus_nums(cfg)}
    ewma = {n: 0.0 for n in bonus_nums(cfg)}
    n_t = len(valides)
    for i, t in enumerate(valides):
        for b in t["bonus"]:
            freq[b] += 1
            dernier[b] = i
            ewma[b] += 0.5 ** ((n_t - 1 - i) / 30)
    return {
        "frequence": normaliser({n: float(freq.get(n, 0)) for n in bonus_nums(cfg)}),
        "retard": normaliser({n: float(n_t - 1 - dernier[n]) for n in bonus_nums(cfg)}),
        "ewma": normaliser(ewma),
        "anti": normaliser({n: -_pop_bonus_heuristique(cfg, n)
                            for n in bonus_nums(cfg)}),
    }


def score_bonus_mode(sb, mode: str):
    base = combiner({k: sb[k] for k in ("frequence", "retard", "ewma")},
                    {"frequence": 1, "retard": 1, "ewma": 1})
    if mode == "anti":
        return dict(sb["anti"])
    if mode == "pronostic":
        return base
    return {n: 0.5 * base[n] + 0.5 * sb["anti"][n] for n in base}


# ==============================================================================
# 6. AGRÉGATION + GÉNÉRATION DE GRILLES
# ==============================================================================

def calculer_scores(cfg, tirages, weekday_prochain, calib):
    couches = {
        "frequence": normaliser(t1_frequence(cfg, tirages)),
        "retard": normaliser(t2_retard(cfg, tirages)),
        "ewma": normaliser(t3_ewma(cfg, tirages)),
        "momentum": normaliser(t4_momentum(cfg, tirages)),
        "markov": normaliser(t5_markov(cfg, tirages)),
        "paires": normaliser(t6_paires(cfg, tirages)),
        "jour": normaliser(t7_jour(cfg, tirages, weekday_prochain)),
    }
    folklore = combiner(couches, POIDS_FOLKLORE)
    anti, anti_mode = scores_anti(cfg, calib)
    return couches, folklore, anti, anti_mode


def score_final(cfg, folklore, anti, mode, poids_anti=0.5):
    if mode == "pronostic":
        return dict(folklore)
    if mode == "anti":
        return dict(anti)
    return {n: (1 - poids_anti) * folklore[n] + poids_anti * anti[n]
            for n in nums(cfg)}


def contraintes_historiques(tirages):
    sommes = sorted(sum(t["balls"]) for t in tirages)
    return {"somme_min": sommes[int(0.12 * len(sommes))],
            "somme_max": sommes[int(0.88 * len(sommes))]}


def grille_valide(cfg, balls, cts) -> bool:
    s = sum(balls)
    if not (cts["somme_min"] <= s <= cts["somme_max"]):
        return False
    pairs = sum(1 for b in balls if b % 2 == 0)
    if pairs not in (2, 3):
        return False
    if len({(b - 1) // 10 for b in balls}) < 3:
        return False
    b, run = sorted(balls), 1
    for k in range(1, len(b)):
        run = run + 1 if b[k] == b[k - 1] + 1 else 1
        if run >= 3:
            return False
    return True


def bonus_delta(cfg, balls, dist_d: Counter) -> float:
    total = sum(dist_d.values()) or 1
    b = sorted(balls)
    deltas = [b[0]] + [b[k] - b[k - 1] for k in range(1, len(b))]
    return 100.0 * sum(dist_d.get(d, 0) / total for d in deltas) / len(deltas)


def generer_grilles(cfg, scores, sb, tirages, mode, n_grilles, rng, calib,
                    iters: int = 30000):
    cts = contraintes_historiques(tirages)
    dist_d = distribution_deltas(cfg, tirages)
    univers = list(nums(cfg))
    poids = [max(scores[n], 1.0) ** 2 for n in univers]
    vues, best = set(), []
    for _ in range(iters):
        pick, garde = set(), 0
        while len(pick) < cfg["pick"] and garde < 120:
            pick.add(rng.choices(univers, weights=poids)[0])
            garde += 1
        if len(pick) != cfg["pick"]:
            continue
        balls = tuple(sorted(pick))
        if balls in vues or not grille_valide(cfg, balls, cts):
            continue
        vues.add(balls)
        g = statistics.mean(scores[b] for b in balls)
        g += 0.15 * bonus_delta(cfg, balls, dist_d)
        if mode in ("anti", "hybride"):
            g -= 6.0 * popularite_grille_penalite(balls)
        best.append((g, balls))
    best.sort(reverse=True)

    sbm = score_bonus_mode(sb, mode)
    tri_bonus = sorted(bonus_nums(cfg), key=lambda n: -sbm[n])
    grilles = []
    for i, (g, balls) in enumerate(best[:n_grilles]):
        if cfg["bonus_pick"] == 1:
            bons = [tri_bonus[i % 3]]
        else:
            bons = sorted([tri_bonus[0], tri_bonus[1 + i % 3]])
        grilles.append({"numeros": list(balls), "bonus": bons,
                        "score": round(g, 1),
                        "pop_rel": round(pop_rel_grille(cfg, balls, calib), 3)})
    return grilles


# ==============================================================================
# 7. EV — ESPÉRANCE DE GAIN EN EUROS (avec partage calibré)
# ==============================================================================

def parametres_ev(cfg, tirages):
    """Estime depuis l'historique :
      · N_est : participation (grilles jouées) par tirage
                = total_gagnants / P(gagner qqch)     [randomisation ⇒ sans biais]
      · ev_fixe : gains hors-jackpot par grille jouée
                = Σ(gagnants_k × rapport_k, rangs ≥ 2) / N_est, moyenné.
    """
    n_ests, ev_fixes = [], []
    for t in tirages[-160:]:
        tot = sum(t["gagnants"].values())
        if tot <= 0:
            continue
        n_est = tot / cfg["p_any_win"]
        n_ests.append(n_est)
        if t["rapports"]:
            paye = sum(t["gagnants"].get(r, 0) * t["rapports"][r]
                       for r in t["rapports"] if r >= 2)
            if paye > 0:
                ev_fixes.append(paye / n_est)
    if not n_ests:
        return None
    return {
        "n_est": round(statistics.median(n_ests)),
        "ev_fixe": round(statistics.mean(ev_fixes), 4) if ev_fixes else None,
        "p_jackpot_inv": proba_jackpot(cfg),
        "prix": cfg["prix"],
    }


def ev_grille(ev_p, jackpot: float, pop_rel: float) -> dict:
    """EV(grille) = ev_fixe + p1·J / (1 + partageurs attendus) − prix,
    partageurs attendus = N_est · p1 · pop_rel(grille)."""
    p1 = 1.0 / ev_p["p_jackpot_inv"]
    partageurs = ev_p["n_est"] * p1 * pop_rel
    comp_jack = p1 * jackpot / (1.0 + partageurs)
    fixe = ev_p["ev_fixe"] or 0.0
    return {"ev": round(fixe + comp_jack - ev_p["prix"], 4),
            "comp_jackpot": round(comp_jack, 4),
            "partageurs_attendus": round(partageurs, 4)}


# ==============================================================================
# 8. SYSTÈMES RÉDUCTEURS — garantie combinatoire VÉRIFIÉE
# ==============================================================================

def systeme_reducteur(cfg, pool: list[int], garantie: int = 3):
    """Couverture gloutonne : ensemble minimal de grilles sur `pool` tel que
    TOUT 5-uplet du pool partage ≥ `garantie` numéros avec au moins une
    grille. Autrement dit : si les 5 numéros sortants sont dans ton pool,
    tu es MATHÉMATIQUEMENT assuré d'avoir ≥ 3 bons numéros quelque part.
    La garantie est re-VÉRIFIÉE exhaustivement par le code avant retour."""
    pool = sorted(pool)
    univers = list(itertools.combinations(pool, cfg["pick"]))
    candidats = list(univers)
    non_couverts = set(univers)
    grilles = []
    while non_couverts:
        meilleur, gain = None, -1
        for c in candidats:
            sc = sum(1 for u in non_couverts
                     if len(set(c) & set(u)) >= garantie)
            if sc > gain:
                meilleur, gain = c, sc
        grilles.append(meilleur)
        non_couverts = {u for u in non_couverts
                        if len(set(meilleur) & set(u)) < garantie}
    # Vérification exhaustive de la garantie
    for u in univers:
        assert any(len(set(g) & set(u)) >= garantie for g in grilles), \
            "garantie non tenue — bug"
    return grilles


# ==============================================================================
# 8bis. HISTORIQUE DES ANNONCES + RÈGLEMENT EN EUROS (le grand livre honnête)
# ==============================================================================
#  Principe : chaque exécution VERROUILLE les grilles annoncées pour le
#  prochain tirage (première annonce = définitive, anti-révisionnisme).
#  Quand le tirage réel apparaît dans les données, chaque grille est réglée
#  avec les VRAIS rapports FDJ de ce tirage : rang obtenu, gain exact.
#  Le cumul (misé / gagné / ROI) est tenu par mode, sans fard.

def rang_gagne(cfg, m: int, b: int) -> int | None:
    """Rang officiel obtenu pour m bons numéros et b bons bonus."""
    if cfg["bonus_pick"] == 1:                     # LOTO (9 rangs)
        table = {(5, 1): 1, (5, 0): 2, (4, 1): 3, (4, 0): 4, (3, 1): 5,
                 (3, 0): 6, (2, 1): 7, (2, 0): 8}
        if (m, b) in table:
            return table[(m, b)]
        if b == 1 and m <= 1:                      # Numéro Chance seul
            return 9
        return None
    # EUROMILLIONS (13 rangs, ordre officiel des gains)
    table = {(5, 2): 1, (5, 1): 2, (5, 0): 3, (4, 2): 4, (4, 1): 5,
             (4, 0): 6, (3, 2): 7, (2, 2): 8, (3, 1): 9, (3, 0): 10,
             (1, 2): 11, (2, 1): 12, (2, 0): 13}
    return table.get((m, b))


def regler_grille(cfg, grille: dict, tirage: dict) -> dict:
    """Règle une grille annoncée contre un tirage réel (rapports FDJ exacts)."""
    bons = sorted(set(grille["numeros"]) & set(tirage["balls"]))
    bonus_ok = sorted(set(grille["bonus"]) & set(tirage["bonus"]))
    rang = rang_gagne(cfg, len(bons), len(bonus_ok))
    gain = 0.0
    if rang is not None:
        gain = tirage["rapports"].get(rang, 0.0) or 0.0
    return {"bons": bons, "bonus_ok": bonus_ok, "rang": rang,
            "gain": round(gain, 2)}


def _chemin_historique(cfg) -> str:
    return os.path.join("data", f"historique_{cfg['nom'].lower()}.json")


def maj_historique(cfg, ctx, args) -> dict:
    """1) Verrouille l'annonce du jour si absente. 2) Règle les annonces en
    attente dont le tirage réel est désormais dans les données. 3) Cumule."""
    chemin = _chemin_historique(cfg)
    hist = {"entrees": {}}
    if os.path.exists(chemin):
        try:
            with open(chemin, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:  # noqa: BLE001
            pass
    entrees = hist.setdefault("entrees", {})

    # -- 1. Annonce (verrouillée à la première écriture) ----------------------
    cle = ctx["date_tirage"].isoformat()
    dernier_connu = ctx["tirages"][-1]["date"].isoformat()
    if cle not in entrees and cle > dernier_connu:
        annonces = {"hybride": None, "pronostic": None, "anti": None}
        for mode in annonces:
            rng_m = random.Random(args.seed if args.seed is not None else 0)
            fin = score_final(cfg, ctx["folklore"], ctx["anti"], mode)
            annonces[mode] = generer_grilles(
                cfg, fin, ctx["sb"], ctx["tirages"], mode,
                max(args.grilles, 3), rng_m, ctx["calib"])
        entrees[cle] = {
            "annonce_le": datetime.now().isoformat(timespec="seconds"),
            "jeu": cfg["nom"], "prix": cfg["prix"],
            "modes": annonces,
            "systeme": ctx["systeme"],
            "regle": False,
        }

    # -- 2. Règlement des annonces en attente ---------------------------------
    par_date = {t["date"].isoformat(): t for t in ctx["tirages"]}
    for d, e in entrees.items():
        if e.get("regle") or d not in par_date:
            continue
        t = par_date[d]
        e["resultat"] = {"numeros": list(t["balls"]), "bonus": list(t["bonus"])}
        for mode, grs in e["modes"].items():
            for g in grs:
                g["reglement"] = regler_grille(cfg, g, t)
        if e.get("systeme"):
            for g in e["systeme"]["grilles"]:
                g["reglement"] = regler_grille(cfg, g, t)
        e["regle"] = True

    # -- 3. Cumuls par mode ----------------------------------------------------
    cumul = {}
    for mode in ("hybride", "pronostic", "anti", "systeme"):
        mises = gains = n = 0.0
        meilleur = None
        for d in sorted(entrees):
            e = entrees[d]
            if not e.get("regle"):
                continue
            grs = (e["modes"].get(mode) if mode != "systeme"
                   else (e.get("systeme") or {}).get("grilles"))
            if not grs:
                continue
            n += 1
            for g in grs:
                mises += e["prix"]
                gains += g["reglement"]["gain"]
                r = g["reglement"]["rang"]
                if r and (meilleur is None or r < meilleur["rang"]):
                    meilleur = {"date": d, "rang": r,
                                "gain": g["reglement"]["gain"]}
        if mises:
            cumul[mode] = {"tirages_regles": int(n), "mise": round(mises, 2),
                           "gain": round(gains, 2),
                           "roi_pct": round(100 * (gains - mises) / mises, 1),
                           "meilleur": meilleur}
    hist["cumul"] = cumul
    hist["maj_le"] = datetime.now().isoformat(timespec="seconds")

    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)

    # Extrait pour l'export web : cumul + les 8 derniers réglés + l'attente
    regles = [dict(e, date=d) for d, e in sorted(entrees.items(), reverse=True)
              if e.get("regle")][:8]
    attente = [d for d, e in sorted(entrees.items()) if not e.get("regle")]
    return {"cumul": cumul, "derniers_regles": regles,
            "en_attente": attente}


# ==============================================================================
# 8ter. RÉTRO-SIMULATION EN EUROS (--simulation N)
# ==============================================================================

def retro_simulation(cfg, tirages, n_derniers: int = 150):
    """Rejoue les N derniers tirages : à chaque tirage, une grille par mode
    (simplifiée : top-5 du score, bonus déterministe), réglée aux rapports
    réels du tirage. Le compteur en euros du 'si on avait joué les annonces'.
    Simplification assumée : 1 grille/mode = top-scores (pas les 30 000
    itérations complètes) — largement suffisant pour mesurer le ROI réel."""
    N = list(nums(cfg))
    debut = max(60, len(tirages) - n_derniers)
    freq, dernier = Counter(), {n: -1 for n in N}
    ewma = {n: 0.0 for n in N}
    decay = 0.5 ** (1 / 30)
    fen = deque()
    bonus_freq = Counter()
    calib, prochaine_calib = None, debut
    res = {m: {"mise": 0.0, "gain": 0.0, "rangs": Counter()}
           for m in ("hybride", "pronostic", "anti")}
    # préchauffe
    for i in range(debut):
        t = tirages[i]
        for n in N:
            ewma[n] *= decay
        for b in t["balls"]:
            freq[b] += 1
            dernier[b] = i
            ewma[b] += 1.0
        fen.append(t["balls"])
        if len(fen) > 20:
            fen.popleft()
        bonus_freq.update(t["bonus"])
    for i in range(debut, len(tirages)):
        if i >= prochaine_calib:
            calib = calibration_empirique(cfg, tirages[:i])
            prochaine_calib = i + 25
        f_n = normaliser({n: float(freq.get(n, 0)) for n in N})
        r_n = normaliser({n: float(i - 1 - dernier[n]) if dernier[n] >= 0
                          else float(i) for n in N})
        e_n = normaliser(dict(ewma))
        cf = Counter()
        for past in fen:
            cf.update(past)
        att = len(fen) * cfg["pick"] / cfg["n_max"]
        m_n = normaliser({n: cf.get(n, 0) - att for n in N})
        folk = {n: (f_n[n] + r_n[n] + e_n[n] + m_n[n]) / 4 for n in N}
        anti, _ = scores_anti(cfg, calib)
        t = tirages[i]
        for mode in res:
            sc = score_final(cfg, folk, anti, mode)
            grille = sorted(sorted(N, key=lambda x: -sc[x])[:cfg["pick"]])
            if mode == "pronostic":
                tb = sorted(bonus_nums(cfg), key=lambda x: -bonus_freq.get(x, 0))
            elif mode == "anti":
                tb = sorted(bonus_nums(cfg),
                            key=lambda x: _pop_bonus_heuristique(cfg, x))
            else:
                tb = sorted(bonus_nums(cfg),
                            key=lambda x: (-bonus_freq.get(x, 0)
                                           + 8 * _pop_bonus_heuristique(cfg, x)))
            bons_b = tb[:cfg["bonus_pick"]]
            r = regler_grille(cfg, {"numeros": grille, "bonus": bons_b}, t)
            res[mode]["mise"] += cfg["prix"]
            res[mode]["gain"] += r["gain"]
            if r["rang"]:
                res[mode]["rangs"][r["rang"]] += 1
        # maj incrémentale
        for n in N:
            ewma[n] *= decay
        for b in t["balls"]:
            freq[b] += 1
            dernier[b] = i
            ewma[b] += 1.0
        fen.append(t["balls"])
        if len(fen) > 20:
            fen.popleft()
        bonus_freq.update(t["bonus"])
    n_sim = len(tirages) - debut
    return {"n_tirages": n_sim, "note": "1 grille/mode = top-scores (simplifié)",
            "modes": {m: {"mise": round(v["mise"], 2),
                          "gain": round(v["gain"], 2),
                          "roi_pct": round(100 * (v["gain"] - v["mise"])
                                           / v["mise"], 1) if v["mise"] else 0,
                          "rangs": dict(sorted(v["rangs"].items()))}
                      for m, v in res.items()}}


# ==============================================================================
# 9. VÉRITÉ : BACKTEST · ANNIVERSAIRE · χ²
# ==============================================================================

def backtest(cfg, tirages, rng, depart: int = 60, fen_mom: int = 20):
    """Walk-forward : à chaque tirage, top-pick du folklore (fréq+retard+
    EWMA+momentum, incrémental) vs hasard. Attendu : pick²/n_max."""
    N = list(nums(cfg))
    freq, dernier = Counter(), {n: -1 for n in N}
    ewma = {n: 0.0 for n in N}
    decay = 0.5 ** (1 / 30)
    fen = deque()
    h_mod, h_froid, h_rand = [], [], []
    for i, t in enumerate(tirages):
        if i >= depart:
            f_n = normaliser({n: float(freq.get(n, 0)) for n in N})
            r_n = normaliser({n: float(i - 1 - dernier[n]) if dernier[n] >= 0
                              else float(i) for n in N})
            e_n = normaliser(dict(ewma))
            cf = Counter()
            for past in fen:
                cf.update(past)
            att = len(fen) * cfg["pick"] / cfg["n_max"]
            m_n = normaliser({n: cf.get(n, 0) - att for n in N})
            agg = {n: f_n[n] + r_n[n] + e_n[n] + m_n[n] for n in N}
            cl = sorted(N, key=lambda n: -agg[n])
            reel = set(t["balls"])
            h_mod.append(len(set(cl[:cfg["pick"]]) & reel))
            h_froid.append(len(set(cl[-cfg["pick"]:]) & reel))
            h_rand.append(len(set(rng.sample(N, cfg["pick"])) & reel))
        for n in N:
            ewma[n] *= decay
        for b in t["balls"]:
            freq[b] += 1
            dernier[b] = i
            ewma[b] += 1.0
        fen.append(t["balls"])
        if len(fen) > fen_mom:
            fen.popleft()
    theo = cfg["pick"] ** 2 / cfg["n_max"]
    return {"n_tests": len(h_mod),
            "modele": statistics.mean(h_mod) if h_mod else 0,
            "froid": statistics.mean(h_froid) if h_froid else 0,
            "aleatoire": statistics.mean(h_rand) if h_rand else 0,
            "theorique": theo}


def test_popularite(tirages):
    """Corrélation (#numéros ≤31 tirés) ↔ log(gagnants). r>0 = effet
    anniversaire prouvé sur les données → anti-partage justifié."""
    xs, ys = [], []
    for t in tirages:
        tot = sum(t["gagnants"].values())
        if tot <= 0:
            continue
        xs.append(sum(1 for b in t["balls"] if b <= 31))
        ys.append(math.log(tot))
    if len(xs) < 50:
        return None
    try:
        r = statistics.correlation(xs, ys)
    except Exception:  # noqa: BLE001
        mx, my = statistics.mean(xs), statistics.mean(ys)
        num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        den = math.sqrt(sum((a - mx) ** 2 for a in xs)
                        * sum((b - my) ** 2 for b in ys)) or 1.0
        r = num / den
    return {"n": len(xs), "r": round(r, 3)}


def test_chi2(cfg, tirages):
    """χ² d'équiprobabilité des boules — le seul edge théorique (usure).
    Seuils ~ ddl±2√(2·ddl) : approximation de Wilson-Hilferty codée en dur
    pour les ddl usuels."""
    SEUILS = {48: (65.17, 73.68), 49: (66.34, 74.92)}
    c = Counter()
    for t in tirages:
        c.update(t["balls"])
    n_total = len(tirages) * cfg["pick"]
    attendu = n_total / cfg["n_max"]
    chi2 = sum((c.get(n, 0) - attendu) ** 2 / attendu for n in nums(cfg))
    ddl = cfg["n_max"] - 1
    s5, s1 = SEUILS.get(ddl, (ddl + 2 * math.sqrt(2 * ddl),
                              ddl + 3.1 * math.sqrt(2 * ddl)))
    return {"chi2": round(chi2, 2), "ddl": ddl,
            "seuil_5pct": round(s5, 2), "seuil_1pct": round(s1, 2),
            "biais_detecte": chi2 > s5,
            "top_suspects": [n for n, _ in c.most_common(3)]}


# ==============================================================================
# 10. AFFICHAGE CONSOLE
# ==============================================================================

def barre(score, largeur=20):
    plein = round(score / 100 * largeur)
    return BAR_FULL * plein + BAR_EMPTY * (largeur - plein)


def prochain_tirage(cfg, aujourdhui: date) -> date:
    d = aujourdhui
    for _ in range(9):
        if d.weekday() in cfg["jours"]:
            return d
        d += timedelta(days=1)
    return aujourdhui


def afficher(cfg, ctx):
    p = print
    t0, t1 = ctx["tirages"][0]["date"], ctx["tirages"][-1]["date"]
    p("\n" + "═" * 68)
    p(f"   🎱  ORACLE v2.0 MAX — {cfg['nom']}")
    p("═" * 68)
    p(f"   Tirages analysés : {len(ctx['tirages'])}  "
      f"({t0:%d/%m/%Y} → {t1:%d/%m/%Y})")
    p(f"   Prochain tirage : {JOURS_FR[ctx['date_tirage'].weekday()]} "
      f"{ctx['date_tirage']:%d/%m/%Y}   |   Mode : {ctx['mode'].upper()}")
    if ctx["jackpot"]:
        p(f"   Jackpot pris en compte : {ctx['jackpot']/1e6:.0f} M€")
    p("─" * 68)

    final = ctx["final"]
    cl = sorted(nums(cfg), key=lambda n: -final[n])
    p(f"\n▶ TOP 12 — SCORE FINAL ({ctx['mode']})")
    for rang, n in enumerate(cl[:12], 1):
        p(f"   {rang:2d}. n°{n:2d}  {barre(final[n])}  {final[n]:5.1f}"
          f"   (folk {ctx['folklore'][n]:5.1f} | anti {ctx['anti'][n]:5.1f})")

    p(f"\n▶ ANTI-PARTAGE — modèle : {ctx['anti_mode']}")
    if ctx["calib"]:
        c = ctx["calib"]
        p(f"   Sur-joués (mesuré) : {c['top_surjoues']}  ← à éviter")
        p(f"   Délaissés (mesuré) : {c['top_delaisses']}  ← or pur")

    sbm = score_bonus_mode(ctx["sb"], ctx["mode"])
    tb = sorted(bonus_nums(cfg), key=lambda n: -sbm[n])
    p(f"\n▶ {cfg['bonus_nom'].upper()} conseillé(es) : "
      + ", ".join(f"n°{n}" for n in tb[:3]))

    p(f"\n▶ GRILLES ({ctx['mode']})")
    for i, g in enumerate(ctx["grilles"], 1):
        nums_s = " - ".join(f"{x:2d}" for x in g["numeros"])
        bon_s = "+".join(str(b) for b in g["bonus"])
        ligne = (f"   G{i} : [ {nums_s} ] + {bon_s}"
                 f"   score {g['score']}, partage ×{g['pop_rel']}")
        if ctx["jackpot"] and ctx["ev_p"]:
            ev = ev_grille(ctx["ev_p"], ctx["jackpot"], g["pop_rel"])
            ligne += f"   EV {ev['ev']:+.2f} €"
        p(ligne)
    if ctx["ev_p"]:
        e = ctx["ev_p"]
        p(f"   (participation estimée ≈ {e['n_est']:,} grilles/tirage · "
          f"gains hors-jackpot ≈ {e['ev_fixe']} €/grille)".replace(",", " "))

    if ctx["systeme"]:
        s = ctx["systeme"]
        p(f"\n▶ SYSTÈME RÉDUCTEUR — pool {s['pool']}")
        p(f"   Garantie VÉRIFIÉE : ≥3 bons numéros si les 5 sortants ∈ pool")
        for i, g in enumerate(s["grilles"], 1):
            p(f"   S{i} : {list(g['numeros'])} + {'+'.join(map(str, g['bonus']))}")
        p(f"   Coût : {s['cout']:.2f} € ({len(s['grilles'])} grilles)")

    bt = ctx["bt"]
    if bt:
        p("\n▶ BACKTEST WALK-FORWARD (juge de paix)")
        p(f"   {bt['n_tests']} tirages rejoués — modèle {bt['modele']:.4f} | "
          f"froid {bt['froid']:.4f} | hasard {bt['aleatoire']:.4f} | "
          f"théorique {bt['theorique']:.4f}")
        delta = bt["modele"] - bt["theorique"]
        seuil = 2 * math.sqrt(0.45 / max(bt["n_tests"], 1))
        if abs(delta) <= seuil:
            p(f"   Verdict : écart {delta:+.4f} dans le bruit — le folklore "
              "ne bat pas le hasard. CQFD.")
        elif abs(delta) <= 1.5 * seuil:
            p(f"   Verdict : écart {delta:+.4f} légèrement hors bruit (~2σ) — "
              "fluctuation probable, à surveiller aux prochaines maj.")
        else:
            p(f"   Verdict : écart {delta:+.4f} nettement hors bruit — "
              "vérifie l'intégrité des données.")

    if ctx["pop"]:
        pp = ctx["pop"]
        p(f"\n▶ EFFET ANNIVERSAIRE : r = {pp['r']:+.3f} (n={pp['n']}) — "
          + ("effet de partage RÉEL dans les données."
             if pp["r"] > 0.05 else "faible sur cet échantillon."))

    c2 = ctx["chi2"]
    p(f"\n▶ BIAIS PHYSIQUE (χ²) : {c2['chi2']} / seuil {c2['seuil_5pct']} — "
      + ("⚠ AU-DESSUS, suspects " + str(c2["top_suspects"])
         if c2["biais_detecte"] else "boules équilibrées, edge fermé."))

    if ctx.get("histo"):
        h = ctx["histo"]
        if h["cumul"]:
            p("\n▶ GRAND LIVRE — ce que les annonces d'Oracle ont VRAIMENT donné")
            for mode, c in h["cumul"].items():
                signe = "+" if c["gain"] >= c["mise"] else ""
                p(f"   {mode:9s} : {c['tirages_regles']} tirages réglés · "
                  f"misé {c['mise']:.2f} € · gagné {c['gain']:.2f} € · "
                  f"ROI {signe}{c['roi_pct']}%")
            der = h["derniers_regles"][0] if h["derniers_regles"] else None
            if der:
                res = der["resultat"]
                p(f"   Dernier réglé ({der['date']}) — sortis : "
                  f"{res['numeros']} + {res['bonus']}")
                for g in der["modes"]["hybride"]:
                    r = g["reglement"]
                    tag = (f"rang {r['rang']} → {r['gain']:.2f} €"
                           if r["rang"] else "rien")
                    p(f"     {g['numeros']} + {g['bonus']} : "
                      f"{len(r['bons'])} bons {r['bons']} · {tag}")
        if h["en_attente"]:
            p(f"   Annonces verrouillées en attente de tirage : "
              f"{', '.join(h['en_attente'])}")

    if ctx.get("sim"):
        s = ctx["sim"]
        p(f"\n▶ RÉTRO-SIMULATION — {s['n_tirages']} tirages rejoués "
          f"({s['note']})")
        for mode, v in s["modes"].items():
            p(f"   {mode:9s} : misé {v['mise']:.2f} € · gagné {v['gain']:.2f} € "
              f"· ROI {v['roi_pct']}%  · rangs touchés {v['rangs'] or '∅'}")
        p("   → Le ROI long terme converge vers -(1-TRJ). C'est la maison qui")
        p("     gagne ; ce compteur est là pour ne jamais l'oublier.")

    pj = proba_jackpot(cfg)
    p("\n" + "─" * 68)
    p(f"   ⚠ P(jackpot) = 1/{pj:,} pour toute grille. L'EV reste négative :".replace(",", " "))
    p("   c'est un jeu. Les seuls leviers réels : partage (calibré), timing")
    p("   (EV), surveillance χ². Le folklore est là pour le fun — et le")
    p("   backtest est là pour le rappeler. Joue avec modération.")
    p("═" * 68)


# ==============================================================================
# 11. EXPORT WEB
# ==============================================================================

def export_web(chemin, cfg, ctx, args):
    modes = {}
    for mode in ("hybride", "pronostic", "anti"):
        rng_m = random.Random(args.seed if args.seed is not None else 0)
        fin = score_final(cfg, ctx["folklore"], ctx["anti"], mode)
        grs = generer_grilles(cfg, fin, ctx["sb"], ctx["tirages"], mode,
                              max(args.grilles, 3), rng_m, ctx["calib"])
        modes[mode] = {
            "scores": {str(n): round(fin[n], 1) for n in nums(cfg)},
            "classement": sorted(nums(cfg), key=lambda n: -fin[n]),
            "bonus": {str(n): round(v, 1) for n, v in
                      score_bonus_mode(ctx["sb"], mode).items()},
            "grilles": grs,
        }
    dern = ctx["tirages"][-1]
    calib = ctx["calib"]
    rapport = {
        "meta": {
            "version": "2.1", "source": "fdj",
            "jeu": args.jeu, "nom": cfg["nom"],
            "bonus_nom": cfg["bonus_nom"], "bonus_pick": cfg["bonus_pick"],
            "bonus_max": cfg["bonus_max"], "n_max": cfg["n_max"],
            "prix": cfg["prix"],
            "genere_le": datetime.now().isoformat(timespec="seconds"),
            "n_tirages": len(ctx["tirages"]),
            "periode_debut": ctx["tirages"][0]["date"].isoformat(),
            "periode_fin": dern["date"].isoformat(),
            "prochain_tirage": ctx["date_tirage"].isoformat(),
            "prochain_jour": JOURS_FR[ctx["date_tirage"].weekday()],
            "proba_jackpot": proba_jackpot(cfg),
            "jackpot": ctx["jackpot"],
            "page_jeu": cfg["page_jeu"],
        },
        "dernier_tirage": {"date": dern["date"].isoformat(),
                           "numeros": list(dern["balls"]),
                           "bonus": list(dern["bonus"])},
        "modes": modes,
        "techniques": {k: {str(n): round(v[n], 1) for n in nums(cfg)}
                       for k, v in ctx["couches"].items()},
        "ev_params": ctx["ev_p"],
        "calibration": (None if not calib else {
            "r2": calib["r2"], "n_tirages": calib["n_tirages"],
            "mode": ctx["anti_mode"],
            "top_surjoues": calib["top_surjoues"],
            "top_delaisses": calib["top_delaisses"],
            "beta": {str(n): round(calib["beta"][n], 4) for n in nums(cfg)},
        }),
        "systeme": ctx["systeme"],
        "historique": ctx.get("histo"),
        "simulation": ctx.get("sim"),
        "verdicts": {"backtest": ctx["bt"],
                     "effet_anniversaire": ctx["pop"],
                     "chi2": ctx["chi2"]},
    }
    os.makedirs(os.path.dirname(os.path.abspath(chemin)), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=1)
    print(f"\n   🌐 Export web écrit : {chemin}")


# ==============================================================================
# 12. MAIN
# ==============================================================================

def main():
    ap = argparse.ArgumentParser(
        description="ORACLE v2.0 MAX — Loto + EuroMillions")
    ap.add_argument("--jeu", choices=list(JEUX), default="loto")
    ap.add_argument("--mode", choices=["pronostic", "anti", "hybride"],
                    default="hybride")
    ap.add_argument("--grilles", type=int, default=4)
    ap.add_argument("--jackpot", type=float, default=None,
                    help="Jackpot en euros (sinon scrape fdj.fr)")
    ap.add_argument("--systeme", type=int, default=None, metavar="N",
                    help="Système réducteur sur les N meilleurs numéros (7-12)")
    ap.add_argument("--csv")
    ap.add_argument("--zip")
    ap.add_argument("--mirror", metavar="URL")
    ap.add_argument("--save-csv", metavar="PATH")
    ap.add_argument("--export-web", metavar="PATH")
    ap.add_argument("--json", metavar="PATH")
    ap.add_argument("--simulation", type=int, nargs="?", const=150,
                    default=None, metavar="N",
                    help="Rétro-simulation en € sur les N derniers tirages")
    ap.add_argument("--aujourdhui", default=None, metavar="AAAA-MM-JJ",
                    help="Force la date du jour (tests / rejeu)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-backtest", action="store_true")
    args = ap.parse_args()

    cfg = JEUX[args.jeu]
    rng = random.Random(args.seed)
    tirages = charger_tirages(cfg, args)
    ref = (date.fromisoformat(args.aujourdhui) if args.aujourdhui
           else date.today())
    date_tirage = prochain_tirage(cfg, ref)

    calib = calibration_empirique(cfg, tirages)
    couches, folklore, anti, anti_mode = calculer_scores(
        cfg, tirages, date_tirage.weekday(), calib)
    final = score_final(cfg, folklore, anti, args.mode)
    sb = bonus_scores(cfg, tirages)
    grilles = generer_grilles(cfg, final, sb, tirages, args.mode,
                              args.grilles, rng, calib)

    jackpot = args.jackpot
    if jackpot is None and not args.csv and not args.zip:
        jackpot = scraper_jackpot(cfg)
    ev_p = parametres_ev(cfg, tirages)

    systeme = None
    if args.systeme:
        n = max(cfg["pick"] + 2, min(args.systeme, 12))
        pool = sorted(nums(cfg), key=lambda x: -final[x])[:n]
        sbm = score_bonus_mode(sb, args.mode)
        tb = sorted(bonus_nums(cfg), key=lambda x: -sbm[x])
        gr = systeme_reducteur(cfg, pool)
        systeme = {
            "pool": sorted(pool),
            "garantie": "≥3 numéros si les 5 sortants sont dans le pool",
            "grilles": [{"numeros": list(g),
                         "bonus": ([tb[i % 3]] if cfg["bonus_pick"] == 1
                                   else sorted([tb[0], tb[1 + i % 3]])),
                         "pop_rel": round(pop_rel_grille(cfg, g, calib), 3)}
                        for i, g in enumerate(gr)],
            "cout": round(len(gr) * cfg["prix"], 2),
        }

    bt = None if args.no_backtest else backtest(cfg, tirages, rng)
    pop = test_popularite(tirages)
    chi2 = test_chi2(cfg, tirages)

    ctx = {"tirages": tirages, "date_tirage": date_tirage, "mode": args.mode,
           "couches": couches, "folklore": folklore, "anti": anti,
           "anti_mode": anti_mode, "calib": calib, "final": final, "sb": sb,
           "grilles": grilles, "jackpot": jackpot, "ev_p": ev_p,
           "systeme": systeme, "bt": bt, "pop": pop, "chi2": chi2}

    ctx["histo"] = maj_historique(cfg, ctx, args)
    ctx["sim"] = (retro_simulation(cfg, tirages, args.simulation)
                  if args.simulation else None)

    afficher(cfg, ctx)
    if args.export_web:
        export_web(args.export_web, cfg, ctx, args)
    if args.json:
        export_web(args.json, cfg, ctx, args)


if __name__ == "__main__":
    main()
