#!/usr/bin/env python3
"""
================================================================================
 ORACLE v2.2 MAX — Loto + EuroMillions
 Pronostiqueur multi-techniques · Calibration empirique · EV jackpot · Systèmes
================================================================================

Un seul fichier, zéro dépendance (stdlib pure). Python 3.10+.

NOUVEAUTÉS v2.2 — données réelles fiabilisées
---------------------------------------------
• MULTI-ÉPOQUES : l'historique FDJ est publié en archives successives, une par
  formule du jeu. On concatène désormais toutes celles dont la structure de
  gains correspond à `rang_gagne` (Loto 1056 → 1473 tirages, EuroMillions
  679 → 1029), en excluant explicitement tirages spéciaux et vieilles
  formules — voir `JEUX[...]["archives"]` et `["exclus"]`.
• AUTO-DÉCOUVERTE RÉPARÉE : la page FDJ est devenue une appli JS ; l'ancien
  regex href+title ne trouvait plus rien, ce qui rendait l'EuroMillions
  (sans URL directe) impossible à télécharger.
• CONTRÔLE D'INTÉGRITÉ : chaque chargement audite les données et publie ses
  constats dans `meta.alertes[]`, affichées sur la page. Rien n'est absorbé
  en silence.

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
#
# ARCHIVES FDJ — pourquoi une LISTE d'époques et pas un seul fichier (v2.2)
# ------------------------------------------------------------------------
# FDJ publie son historique en archives successives, une par « formule » du
# jeu. Deux règles président à la sélection ci-dessous :
#
#   1. On ne concatène QUE des époques dont la structure de gains est
#      identique à celle codée dans `rang_gagne` (sinon le grand livre
#      réglerait des grilles avec la mauvaise table de rangs).
#   2. On EXCLUT les tirages spéciaux (Super Loto, Grand Loto, Loto de Noël) :
#      prix et grille de gains différents, et surtout jours de tirage
#      atypiques qui pollueraient la technique T7 « jour ». Ils vivent dans
#      des fichiers séparés chez FDJ — il suffit de ne pas les lister.
#
# `exclus` documente ce qu'on écarte volontairement : sans cette trace, un
# futur lecteur croirait à un oubli et « réparerait » le bug en le créant.

VERSION = "2.8"          # bump obligatoire à tout changement du contrat JSON

FDJ_DOC = ("https://www.sto.api.fdj.fr/anonymous/service-draw-info/"
           "v3/documentations/")

JEUX = {
    "loto": {
        "nom": "LOTO",
        "n_max": 49, "pick": 5,
        "bonus_max": 10, "bonus_pick": 1, "bonus_nom": "chance",
        "bonus_patterns": [r"numero[_ ]?chance"],
        "prix": 2.20,
        # Tarifs successifs de la grille simple, du plus récent au plus ancien.
        # Le « nouveau Loto » du 04/11/2019 a porté la grille de 2,00 à 2,20 €.
        # Rejouer le passé au tarif d'aujourd'hui gonfle la mise et noircit
        # le ROI affiché : la rétro-simulation applique le prix de l'époque.
        "prix_historique": [("2019-11-04", 2.20), ("1900-01-01", 2.00)],
        # Combinatoire exacte : (142 121 × 10 + 1 764 763) / 19 068 840.
        # La FDJ communique « 1 chance sur 6 » ; la vraie valeur est 1/5,985.
        # C'est le diviseur de n_est, donc de toute l'EV : on prend l'exact.
        "p_any_win": 3_185_973 / 19_068_840,          # 0,1670772
        "jours": {0, 2, 5},          # lundi, mercredi, samedi
        "page_hist": "https://www.fdj.fr/jeux-de-tirage/loto/historique",
        "page_jeu": "https://www.fdj.fr/jeux-de-tirage/loto",
        "titre_lien": r"historique\s+loto",
        # Époques 5/49 + Chance 1-10 à 9 rangs (structure de `rang_gagne`).
        "archives": [
            {"id": "1a2b3c4d-9876-4562-b3fc-2c963f66afn6",
             "label": "loto_2017", "depuis": "2017-03-06", "close": True},
            {"id": "1a2b3c4d-9876-4562-b3fc-2c963f66afo6",
             "label": "loto_201902", "depuis": "2019-02-27", "close": True},
            {"id": "1a2b3c4d-9876-4562-b3fc-2c963f66afp6",
             "label": "loto_201911", "depuis": "2019-11-06", "close": False},
        ],
        "exclus": {
            "nouveau_loto (2008-10 → 2017-03)": "6 rangs, table de gains ≠",
            "loto.csv (1976 → 2008-10)": "formule 6/49, sans n° Chance",
            "superloto · grandloto · lotonoel": "tirages spéciaux (prix et "
                                                "jours différents)",
        },
    },
    "euromillions": {
        "nom": "EUROMILLIONS",
        "n_max": 50, "pick": 5,
        "bonus_max": 12, "bonus_pick": 2, "bonus_nom": "étoiles",
        "bonus_patterns": [r"etoile[_ ]?1", r"etoile[_ ]?2"],
        "prix": 2.50,
        # Combinatoire exacte : (152 026 × 66 + 744 975) / 139 838 160.
        # La FDJ communique « 1 chance sur 13 » ; la vraie valeur est 1/12,974.
        "p_any_win": 10_778_691 / 139_838_160,        # 0,0770792
        "jours": {1, 4},             # mardi, vendredi
        "page_hist": ("https://www.fdj.fr/jeux-de-tirage/"
                      "euromillions-my-million/historique"),
        "page_jeu": "https://www.fdj.fr/jeux-de-tirage/euromillions-my-million",
        "titre_lien": r"historique\s+euro\s*millions",
        # Époques à 12 étoiles / 13 rangs (depuis le 24/09/2016).
        "archives": [
            {"id": "1a2b3c4d-9876-4562-b3fc-2c963f66afc6",
             "label": "euromillions_4", "depuis": "2016-09-27", "close": True},
            {"id": "1a2b3c4d-9876-4562-b3fc-2c963f66afd6",
             "label": "euromillions_201902", "depuis": "2019-03-01",
             "close": True},
            {"id": "1a2b3c4d-9876-4562-b3fc-2c963f66afe6",
             "label": "euromillions_202002", "depuis": "2020-02-04",
             "close": False},
        ],
        "exclus": {
            "euromillions_3 · euromillions_2 (2011-05 → 2016-09)":
                "11 étoiles — bonus_max ≠",
            "euromillions.csv (2004 → 2011-05)": "9 étoiles — bonus_max ≠",
        },
    },
}

JOURS_FR = {0: "lundi", 1: "mardi", 2: "mercredi", 3: "jeudi",
            4: "vendredi", 5: "samedi", 6: "dimanche"}

POIDS_FOLKLORE = {"frequence": 1.0, "retard": 1.0, "ewma": 1.0,
                  "momentum": 1.0, "markov": 1.0, "paires": 1.0, "jour": 0.5}

BAR_FULL, BAR_EMPTY = "█", "░"


def prix_du_tirage(cfg, jour: date) -> float:
    """Prix d'une grille simple à la date `jour`.

    Sans `prix_historique`, le tarif est réputé constant. Sinon on prend le
    premier palier dont la date d'entrée en vigueur précède le tirage.
    """
    paliers = cfg.get("prix_historique")
    if not paliers:
        return cfg["prix"]
    iso = jour.isoformat()
    for depuis, montant in paliers:
        if iso >= depuis:
            return montant
    return cfg["prix"]


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
    """Scrape la page d'historique FDJ et extrait les identifiants d'archives.

    v2.2 — la page FDJ est désormais une appli JS : les URLs vivent dans le
    payload JSON, plus dans des `<a href=… title=…>`. L'ancien regex (qui
    exigeait href ET title) ne trouvait donc plus RIEN — panne silencieuse
    fatale pour EuroMillions, qui n'avait aucune URL directe en secours.
    On cherche maintenant le motif stable `documentations/<id>` où qu'il soit.
    """
    try:
        html = _http_get(cfg["page_hist"]).decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"    (scrape page FDJ impossible : {type(e).__name__})")
        return []
    ids = re.findall(r"documentations/([0-9a-zA-Z][0-9a-zA-Z\-]{20,})", html)
    return list(dict.fromkeys(FDJ_DOC + i for i in ids))


def _ecrire_archive(data: bytes, dest_dir: str, label: str) -> str:
    ext = ".zip" if data[:2] == b"PK" else ".csv"
    path = os.path.join(dest_dir, f"{label}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return path


def telecharger_archives(cfg, dest_dir: str,
                         mirror: str | None = None) -> list[tuple[str, str]]:
    """Récupère TOUTES les époques déclarées du jeu → [(label, chemin)].

    Par archive, ordre de secours : URL directe déclarée → auto-découverte
    → mirror perso. Les époques closes (`close: True`) ne bougeront plus
    jamais chez FDJ : si le fichier est déjà en cache local on le réutilise,
    ce qui rend le cron rapide et résilient à une panne FDJ partielle.
    Une époque manquante n'est pas fatale : on continue avec les autres et
    le contrôle d'intégrité le signalera dans `meta.alertes`.
    """
    os.makedirs(dest_dir, exist_ok=True)
    decouverts: list[str] | None = None
    obtenus: list[tuple[str, str]] = []

    for arc in cfg["archives"]:
        label = arc["label"]
        cache = [os.path.join(dest_dir, label + e) for e in (".zip", ".csv")]
        cache = [p for p in cache if os.path.exists(p)]
        if arc["close"] and cache:
            print(f"  ↺ {label} : époque close, cache local réutilisé")
            obtenus.append((label, cache[0]))
            continue

        data = None
        try:
            data = _http_get(FDJ_DOC + arc["id"])
            if len(data) < 500:
                data = None
        except Exception as e:  # noqa: BLE001
            print(f"    ✗ {label} : {type(e).__name__} sur l'URL directe")

        if data is None:                      # secours 1 : auto-découverte
            if decouverts is None:
                print("  → auto-découverte des archives sur la page FDJ…")
                decouverts = decouvrir_liens_fdj(cfg)
                print(f"    {len(decouverts)} archive(s) listée(s)")
            for url in decouverts:
                try:
                    d = _http_get(url)
                except Exception:  # noqa: BLE001
                    continue
                if len(d) < 500 or not _archive_correspond(cfg, d, arc):
                    continue
                data = d
                print(f"    ✔ {label} retrouvée par auto-découverte")
                break

        if data is None and mirror:           # secours 2 : mirror perso
            try:
                data = _http_get(mirror.replace("{label}", label))
                print(f"    ✔ {label} récupérée depuis le mirror")
            except Exception:  # noqa: BLE001
                data = None

        if data is None and cache:            # secours 3 : cache périmé
            print(f"    ↺ {label} : réseau KO, cache local (peut-être daté)")
            obtenus.append((label, cache[0]))
            continue
        if data is None:
            print(f"    ✗ {label} : introuvable, époque ignorée")
            continue

        path = _ecrire_archive(data, dest_dir, label)
        print(f"  ✔ {label} ({len(data)//1024} Ko)")
        obtenus.append((label, path))
    return obtenus


def _archive_correspond(cfg, data: bytes, arc: dict) -> bool:
    """L'auto-découverte renvoie TOUTES les archives du jeu, y compris les
    tirages spéciaux et les vieilles formules. On identifie la bonne par le
    nom du CSV qu'elle contient, sinon par sa date de début."""
    try:
        texte = _texte_archive(data)
    except Exception:  # noqa: BLE001
        return False
    tirages = parser_csv(cfg, texte, tolerant=True)
    if not tirages:
        return False
    return tirages[0]["date"].isoformat() == arc["depuis"]


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


def _texte_archive(brut: bytes) -> str:
    """Rend le texte CSV d'une archive FDJ, qu'elle soit ZIP ou CSV nu.
    Dans un ZIP, on prend le plus gros CSV (les archives FDJ n'en contiennent
    qu'un, mais certaines embarquent un fichier de notes)."""
    if brut[:2] != b"PK":
        return _decoder(brut)
    with zipfile.ZipFile(io.BytesIO(brut)) as z:
        noms = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not noms:
            raise ValueError("aucun CSV dans l'archive")
        return _decoder(z.read(max(noms, key=lambda n: z.getinfo(n).file_size)))


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


def parser_csv(cfg, texte: str, tolerant: bool = False) -> list[dict]:
    """Parse un CSV FDJ (Loto ou EuroMillions), tolérant aux variantes.
    Retourne des tirages triés par date : {date, jour, balls, bonus,
    gagnants{rang:int}, rapports{rang:float}}.

    `tolerant=True` : rend [] au lieu de lever quand les colonnes attendues
    manquent — utilisé pour trier à l'aveugle les archives de l'auto-
    découverte (elles mélangent formules et jeux)."""
    lignes = [ligne for ligne in texte.splitlines() if ligne.strip()]
    if not lignes:
        return []
    delim = _sniff_delim(lignes[0])
    reader = csv.reader(io.StringIO("\n".join(lignes)), delimiter=delim)
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

    # Colonnes à écarter avant toute chose.
    #  · "second", "my_million", "etoile+" : jeux annexes. Le CSV EuroMillions
    #    porte une SECONDE famille de rangs 1-13, celle d'Étoile+, dont les
    #    gagnants n'ont aucun rapport avec ceux du tirage principal.
    #  · "en_europe" : chaque rang existe en version française ET européenne.
    #    Le moteur raisonne en gagnants FRANÇAIS (c'est ce que `p_any_win` et
    #    `n_est` supposent). Jusqu'ici seul l'ORDRE des colonnes — France
    #    d'abord — évitait de lire l'Europe : une propriété du fichier FDJ, pas
    #    du moteur. Vérifié en permutant les colonnes : le total des gagnants
    #    était multiplié par 6,1, donc n_est par 6,1 et le TRJ divisé d'autant,
    #    sans que rien ne plante. Cf. test_parser.py.
    def a_ignorer(h: str) -> bool:
        return ("second" in h or "my_million" in h or "etoile+" in h
                or "en_europe" in h)

    cols_gagnants, cols_rapports = {}, {}
    for i, h in enumerate(header):
        if a_ignorer(h):
            continue
        r = rang_de(h)
        if r is None:
            continue
        if "nombre_de_gagnant" in h and r not in cols_gagnants:
            cols_gagnants[r] = i
        elif "rapport" in h and r not in cols_rapports:
            cols_rapports[r] = i

    # Les colonnes bonus sont EXIGÉES : sans elles, un fichier d'une vieille
    # formule (Loto 6/49 d'avant 2008, qui a bien boule_1..5 mais pas de
    # n° Chance) se parserait silencieusement en perdant sa 6e boule.
    manque = (col_date is None or any(c is None for c in cols_boules)
              or any(c is None for c in cols_bonus))
    if manque:
        if tolerant:
            return []
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


def controle_integrite(cfg, tirages, sources, aujourdhui: date,
                       doublons=(), multi_epoques: bool = True) -> list[dict]:
    """Audit des données chargées → liste d'alertes exportée en `meta.alertes`.

    Le principe du produit vaut aussi pour ses données : on ne masque rien.
    Une anomalie visible dans l'UI vaut mieux qu'un pronostic calculé en
    silence sur un historique troué.
    """
    al: list[dict] = []

    def dire(niveau, message):
        al.append({"niveau": niveau, "message": message})

    # -- couverture des époques déclarées --------------------------------------
    # En mode --zip/--csv l'utilisateur impose SA source : lui reprocher les
    # époques absentes serait du bruit. On le dit, sans crier.
    if not multi_epoques:
        dire("attention", "Source unique imposée (--zip/--csv) : l'historique "
             "multi-époques n'a pas été chargé.")
    else:
        attendues = {a["label"] for a in cfg["archives"]}
        manquantes = attendues - {lbl for lbl, _ in sources}
        if manquantes:
            dire("attention", "Époque(s) non chargée(s) : "
                 + ", ".join(sorted(manquantes))
                 + " — historique amputé, calibration moins fiable.")

    if not tirages:
        dire("critique", "Aucun tirage exploitable.")
        return al

    # -- doublons et monotonie -------------------------------------------------
    if doublons:
        dbl = sorted(set(doublons))
        dire("attention", f"{len(dbl)} date(s) présente(s) dans deux archives "
             f"(ex. {dbl[0]}) — l'époque la plus récente a été retenue.")
    if any(a["date"] >= b["date"] for a, b in zip(tirages, tirages[1:], strict=False)):
        dire("critique", "Dates non strictement croissantes après tri — "
             "données corrompues.")

    # -- trous > 7 jours -------------------------------------------------------
    trous = [(a["date"], b["date"], (b["date"] - a["date"]).days)
             for a, b in zip(tirages, tirages[1:], strict=False)
             if (b["date"] - a["date"]).days > 7]
    if trous:
        ex = ", ".join(f"{a}→{b} ({n} j)" for a, b, n in trous[:3])
        dire("attention", f"{len(trous)} trou(s) de plus de 7 jours : {ex}"
             + (" …" if len(trous) > 3 else ""))

    # -- jours de tirage inattendus (= tirage spécial infiltré) ----------------
    intrus = [t["date"] for t in tirages if t["jour"] not in cfg["jours"]]
    if intrus:
        dire("attention", f"{len(intrus)} tirage(s) un jour inhabituel "
             f"(ex. {intrus[0]}) — tirage spécial mêlé à l'historique ?")

    # -- intégrité ligne à ligne ----------------------------------------------
    bonus_ko = sum(1 for t in tirages if len(t["bonus"]) != cfg["bonus_pick"])
    if bonus_ko:
        dire("attention", f"{bonus_ko} tirage(s) sans {cfg['bonus_nom']} "
             "complet — exclus des scores bonus.")
    # Un bonus maximal JAMAIS tiré trahit une époque à formule différente
    # (EuroMillions 9 ou 11 étoiles avant sept. 2016) : elle se parse sans
    # erreur puisque ses valeurs restent dans la plage — seule cette absence
    # statistique la démasque.
    if len(tirages) > 200:
        vus_bonus = {b for t in tirages for b in t["bonus"]}
        absents = [n for n in bonus_nums(cfg) if n not in vus_bonus]
        if absents:
            dire("critique", f"{cfg['bonus_nom'].capitalize()} jamais tiré(es) "
                 f"sur {len(tirages)} tirages : {absents} — une époque d'une "
                 "ancienne formule s'est glissée dans l'historique.")

    sans_gagnants = sum(1 for t in tirages if not t["gagnants"])
    if sans_gagnants > len(tirages) * 0.05:
        dire("attention", f"{sans_gagnants} tirage(s) sans colonne gagnants "
             "— calibration et EV portent sur moins de données.")

    # -- fraîcheur -------------------------------------------------------------
    dernier = tirages[-1]["date"]
    attendu = None
    for k in range(1, 10):
        c = aujourdhui - timedelta(days=k)
        if c.weekday() in cfg["jours"]:
            attendu = c
            break
    if attendu and dernier < attendu:
        dire("attention", f"Dernier tirage connu {dernier}, or un tirage a eu "
             f"lieu le {attendu} — historique en retard.")

    # -- volume ----------------------------------------------------------------
    if len(tirages) < 300:
        dire("attention", f"Seulement {len(tirages)} tirages : la calibration "
             "empirique reste bruitée sous ~300.")
    if not al:
        dire("info", f"{len(tirages)} tirages, aucune anomalie détectée "
             f"({tirages[0]['date']} → {dernier}).")
    return al


def charger_tirages(cfg, args, aujourdhui: date):
    """Rend (tirages, sources, alertes). Concatène les époques compatibles."""
    sources: list[tuple[str, str]] = []
    textes: list[tuple[str, str]] = []

    if args.csv or args.zip:                   # mode fichier unique (tests)
        chemin = args.csv or args.zip
        with open(chemin, "rb") as f:
            brut = f.read()
        textes.append((os.path.basename(chemin), _texte_archive(brut)))
        sources.append((os.path.basename(chemin), chemin))
        if getattr(args, "save_csv", None):
            os.makedirs(os.path.dirname(os.path.abspath(args.save_csv)),
                        exist_ok=True)
            with open(args.save_csv, "w", encoding="utf-8") as f:
                f.write(textes[0][1])
            print(f"  💾 CSV sauvegardé : {args.save_csv}")
    else:
        print(f"Historique {cfg['nom']} — {len(cfg['archives'])} époques…")
        sources = telecharger_archives(
            cfg, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "data"),
            mirror=getattr(args, "mirror", None))
        if not sources:
            print(f"\n✗ Aucune archive récupérée.\n"
                  f"  Télécharge un ZIP à la main : {cfg['page_hist']}\n"
                  f"  puis relance avec --zip fichier.zip\n")
            sys.exit(1)
        for label, chemin in sources:
            with open(chemin, "rb") as f:
                textes.append((label, _texte_archive(f.read())))

    # Fusion : les époques récentes priment en cas de date en double.
    par_date: dict[date, dict] = {}
    doublons: list[date] = []
    for label, texte in textes:
        for t in parser_csv(cfg, texte):
            t["source"] = label
            if t["date"] in par_date:
                doublons.append(t["date"])
            par_date[t["date"]] = t
    tirages = sorted(par_date.values(), key=lambda t: t["date"])

    alertes = controle_integrite(cfg, tirages, sources, aujourdhui,
                                 doublons=doublons,
                                 multi_epoques=not (args.csv or args.zip))
    if len(tirages) < 60:
        print(f"✗ Seulement {len(tirages)} tirages exploitables — trop peu.")
        sys.exit(1)
    print(f"  ⚑ {len(tirages)} tirages ({tirages[0]['date']} → "
          f"{tirages[-1]['date']}) depuis {len(sources)} archive(s)")
    return tirages, sources, alertes


# ==============================================================================
# 2. NORMALISATION
# ==============================================================================

def normaliser(scores: dict[int, float]) -> dict[int, float]:
    """Étale les scores sur [0, 100], bornes incluses et GARANTIES.

    Le bornage explicite n'est pas décoratif : 100·(v−lo)/(hi−lo) peut rendre
    100,000000000000014 par simple arrondi flottant. Mesuré, le dépassement
    plafonne à 1,4·10⁻¹⁴ — inoffensif ici, mais un invariant annoncé qui ne
    tient pas est un invariant sur lequel on finira par s'appuyer à tort.
    """
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    if math.isclose(hi, lo):
        return dict.fromkeys(scores, 50.0)
    ecart = hi - lo
    return {k: min(100.0, max(0.0, 100.0 * (v - lo) / ecart))
            for k, v in scores.items()}


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
    dernier = dict.fromkeys(nums(cfg), -1)
    for i, t in enumerate(tirages):
        for b in t["balls"]:
            dernier[b] = i
    n_t = len(tirages)
    return {n: float(n_t - 1 - dernier[n]) if dernier[n] >= 0 else float(n_t)
            for n in nums(cfg)}


def t3_ewma(cfg, tirages, demi_vie: int = 30):
    """T3 — Chauds récents pondérés exponentiellement. Bruit lissé."""
    n_t = len(tirages)
    s = dict.fromkeys(nums(cfg), 0.0)
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
    for prev, cur in zip(tirages, tirages[1:], strict=False):
        for i in prev["balls"]:
            M[i].update(cur["balls"])
    dernier = tirages[-1]["balls"]
    s = dict.fromkeys(nums(cfg), 0.0)
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


# ---- T9b : CALIBRATION EMPIRIQUE (panel à effets fixes de tirage) ------------
#
# Ce qui a changé en v2.4, et pourquoi.
#
# La v2.3 régressait log(TOTAL des gagnants) sur les numéros sortis, avec une
# tendance t/t² et des indicatrices de jour pour absorber la participation.
# Deux défauts mesurés :
#
#   1. Au Loto, le rang 9 (« n° Chance seul ») pèse ~55 % des gagnants et ne
#      dépend PAS des 5 boules. Plus de la moitié de la variable expliquée
#      était donc du bruit vis-à-vis du signal cherché. |t| médian sur les
#      49 coefficients : 1,87 — seuls 24/49 sortaient du bruit.
#   2. Une tendance t/t² ne peut pas absorber les rollovers de jackpot, qui
#      sont le premier moteur de la participation et sautent d'un tirage à
#      l'autre.
#
# La v2.4 exploite le fait que la FDJ publie les gagnants RANG PAR RANG. Pour
# un même tirage, chaque rang voit la MÊME participation mais un nombre
# différent de boules appariées. On peut donc mettre un effet fixe par tirage
# et éliminer la participation EXACTEMENT, au lieu de l'approximer :
#
#     log W(t,r) = alpha_t + mu_r
#                  + (m_r/pick)  · Σ_j x(t,j)·gamma_j
#                  + (b_r/bpick) · Σ_k z(t,k)·delta_k
#                  + Σ_f (C(m_r,2)/C(pick,2)) · F_f(tirage) · theta_f + eps
#
#   · alpha_t : participation du tirage t (jackpot, saison, météo, pub…).
#     Éliminé par centrage intra-tirage. Aucun contrôle à spécifier, donc
#     aucun contrôle à mal spécifier.
#   · gamma_j : log-popularité du numéro j chez les joueurs. CE QU'ON CHERCHE.
#   · delta_k : idem pour le n° Chance / les Étoiles — mesuré, plus deviné.
#   · theta_f : affinités de CO-OCCURRENCE (les joueurs cochent des dates
#     ENSEMBLE). Le facteur C(m,2)/C(pick,2) est ce qui les rend identifiables
#     séparément des gamma marginaux : un effet marginal croît en m, un effet
#     de paire croît en m².
#
# Le facteur m/pick vient du développement de log W_m : chaque boule tirée
# apparaît dans C(pick−1,m−1)/C(pick,m) = m/pick des sous-ensembles de
# taille m appariables. Vérifié sur les données (cf. tests/test_popularite.py) :
# gamma estimé sur les rangs à m faible et sur ceux à m fort donne la même
# échelle à 3 % près, alors que les facteurs alternatifs (m/pick)² et
# sqrt(m/pick) détruisent le signal.
#
# CE QUI REND CETTE RÉGRESSION LÉGITIME : x(t,·) est TIRÉ AU SORT par la FDJ.
# La variable explicative est randomisée, donc orthogonale à alpha_t et à tout
# confondant imaginable. Ce n'est pas une étude observationnelle, c'est une
# expérience randomisée dont la FDJ publie les résultats.
#
# Mesures de contrôle (toutes dans tests/test_popularite.py) :
#   · placebo (boules remplacées par un tirage indépendant) : |t| médian 0,71,
#     2 coefficients sur 49 au-delà de 1,96 — soit exactement le taux d'erreur
#     de première espèce attendu. L'estimateur ne fabrique pas de signal.
#   · stabilité 1re moitié vs 2e moitié : Spearman +0,97 (Loto), +0,90 (Euro).
#   · insensible à lambda (0,5→32), w_min (10→1000), phi (0→0,10).

# Co-occurrences retenues : significatives à |t| > 3 dans les DEUX jeux,
# de même signe, et nulles sous placebo. Les trois règles codées en dur de la
# v2.3 sont supprimées : « tous ≤ 15 » n'est jamais arrivé en 1473 tirages
# (inestimable) et « suite arithmétique » deux fois (2 occurrences ne
# soutiennent aucun coefficient). « nb ≤ 31 ≥ 4 » est remplacé par sa version
# continue et correctement pondérée, `date_31`.
PAIRES_POPULARITE = (
    # les joueurs cochent des dates : deux numéros de jour cochés ensemble
    ("date_31", lambda x, y: x <= 31 and y <= 31),
    # …et le numéro du mois compte double (jour ET mois)
    ("mois_12", lambda x, y: x <= 12 and y <= 12),
    # motifs de la grille papier
    ("consecutifs", lambda x, y: y - x == 1),
    ("meme_dizaine", lambda x, y: (x - 1) // 10 == (y - 1) // 10),
    # signature d'un MÉLANGE de populations : les numéros hauts sont joués
    # ensemble parce que seuls les joueurs « hors dates » les cochent. Sans ce
    # terme, on surestime le bénéfice d'une grille tout-en-haut.
    ("hauts_31", lambda x, y: x > 31 and y > 31),
)

# En deçà, on n'estime pas : on retombe sur le prior de la littérature.
MIN_TIRAGES_CALIBRATION = 200


def compter_paires(balls, pred) -> float:
    """Nombre de paires du tirage vérifiant `pred` (les deux numéros triés)."""
    b = sorted(balls)
    return float(sum(1 for i in range(len(b)) for j in range(i + 1, len(b))
                     if pred(b[i], b[j])))


def traits_paires(balls) -> list[float]:
    return [compter_paires(balls, pred) for _, pred in PAIRES_POPULARITE]


def _poids_par_paire(theta: dict[str, float], n_max: int):
    """Pré-calcule theta·traits pour CHAQUE paire (x, y) de numéros.

    `generer_grilles` évalue jusqu'à 30 000 grilles candidates ; recalculer
    les cinq prédicats sur les dix paires de chacune coûtait ~50 appels de
    fonction par grille. Ici on paie n_max²/2 évaluations UNE fois, et le
    score d'une grille se réduit à dix additions.
    """
    table = [[0.0] * (n_max + 1) for _ in range(n_max + 1)]
    for x in range(1, n_max + 1):
        for y in range(x + 1, n_max + 1):
            v = sum(theta[nom] for nom, pred in PAIRES_POPULARITE
                    if pred(x, y))
            table[x][y] = table[y][x] = v
    return table


def somme_paires(balls, table) -> float:
    """Σ theta sur les paires de la grille, par table pré-calculée.

    `table` est symétrique : pas besoin de trier, et l'indexation par entiers
    évite de construire puis hacher un tuple par paire — ce qui compte, cette
    fonction étant appelée jusqu'à 30 000 fois par génération de grilles.
    """
    s = 0.0
    for i in range(len(balls) - 1):
        ligne = table[balls[i]]
        for j in range(i + 1, len(balls)):
            s += ligne[balls[j]]
    return s


def _log_normalisation(cfg, gamma, table, delta, n_ech: int = 40_000):
    """Constante qui rend le multiplicateur de partage égal à 1 EN MOYENNE.

    Pourquoi elle est indispensable
    -------------------------------
    `ev_grille` calcule les co-gagnants attendus par
        partageurs = n_est · p1 · pop_rel
    ce qui n'a de sens que si pop_rel vaut 1 pour une grille quelconque : p1
    est déjà la probabilité d'une combinaison sous jeu uniforme, pop_rel n'a
    donc à porter QUE l'écart à l'uniforme.

    Or centrer gamma centre le LOGARITHME, pas le multiplicateur : par
    inégalité de Jensen, E[exp(indice)] > exp(E[indice]) = 1. Mesuré, la
    grille médiane sortait à 1,48 et la moyenne à ~2 — tous les pop_rel
    étaient donc surestimés d'un facteur 2, et les co-gagnants avec eux.

    En v2.3 l'écart était de ~1 % (les beta étaient 5,4× plus petits, et
    exp(x) ≈ 1+x y suffisait). En v2.4, à la bonne échelle, il ne l'est plus.

    Numéros : moyenne de Monte-Carlo sur des combinaisons tirées uniformément
    (les termes de paires interdisent une forme produit). Graine fixe : la
    calibration doit rester reproductible.
    Bonus : énumération EXACTE, il y a au plus C(12,2) = 66 cas.
    """
    rng = random.Random(0xC0FFEE)
    univers = list(nums(cfg))
    g = [0.0] * (cfg["n_max"] + 1)
    for n in univers:
        g[n] = gamma[n]
    exp_, sample, total = math.exp, rng.sample, 0.0
    for _ in range(n_ech):
        b = sample(univers, cfg["pick"])
        s = 0.0
        for i, x in enumerate(b):
            s += g[x]
            ligne = table[x]
            for j in range(i + 1, len(b)):
                s += ligne[b[j]]
        total += exp_(s)
    log_nums = math.log(total / n_ech)

    combis = list(itertools.combinations(bonus_nums(cfg), cfg["bonus_pick"]))
    log_bonus = math.log(
        sum(math.exp(sum(delta[k] for k in c)) for c in combis) / len(combis))
    return log_nums, log_bonus


def _inverser(A):
    """Inverse par Gauss-Jordan avec pivot partiel (stdlib)."""
    n = len(A)
    M = [row[:] + [1.0 if i == j else 0.0 for j in range(n)]
         for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-13:
            raise ValueError("matrice singulière")
        M[col], M[piv] = M[piv], M[col]
        d = M[col][col]
        M[col] = [v / d for v in M[col]]
        for r in range(n):
            if r != col and M[r][col] != 0:
                f = M[r][col]
                M[r] = [a - f * c for a, c in zip(M[r], M[col], strict=True)]
    return [row[n:] for row in M]


def rangs_mb(cfg) -> dict[int, tuple[float, float]]:
    """(m, b) EFFECTIFS de chaque rang, par dénombrement combinatoire exact.

    La plupart des rangs correspondent à un couple (m, b) unique. Le rang 9 du
    Loto agrège m ∈ {0, 1} : son m effectif est E[m | rang 9] = 0,3846, pas 0
    ni 1. L'employer à tort décalerait l'échelle de tous les gamma.
    """
    n, k = cfg["n_max"], cfg["pick"]
    bmax, bp = cfg["bonus_max"], cfg["bonus_pick"]
    acc: dict[int, tuple[float, float, float]] = {}
    for m in range(k + 1):
        for b in range(bp + 1):
            r = rang_gagne(cfg, m, b)
            if r is None:
                continue
            cas = (math.comb(k, m) * math.comb(n - k, k - m)
                   * math.comb(bp, b) * math.comb(bmax - bp, bp - b))
            sm, sb, sc = acc.get(r, (0.0, 0.0, 0.0))
            acc[r] = (sm + m * cas, sb + b * cas, sc + cas)
    return {r: (sm / sc, sb / sc) for r, (sm, sb, sc) in acc.items()}


def rangs_denses(cfg, tirages, w_min: int = 30, part: float = 0.99):
    """Rangs peuplés d'au moins `w_min` gagnants dans ≥ `part` des tirages.

    Un rang seulement parfois disponible introduirait une SÉLECTION sur la
    variable expliquée : on ne garderait que les tirages où ce rang est bien
    garni, c'est-à-dire les tirages populaires — exactement ce qu'on mesure.
    Au Loto cela écarte les rangs 1 à 3 (0, 1 et 43 gagnants médians).
    """
    if not tirages:
        return []
    ok = []
    for r in sorted(rangs_mb(cfg)):
        n = sum(1 for t in tirages if (t["gagnants"].get(r) or 0) >= w_min)
        if n >= part * len(tirages):
            ok.append(r)
    return ok


def calibration_empirique(cfg, tirages, lam: float = 2.0, w_min: int = 30,
                          phi: float = 0.03, rangs_forces=None,
                          avec_paires: bool = True):
    """Panel à effets fixes de tirage. Voir le commentaire de section.

    `phi` : sur-dispersion résiduelle. Le poids d'une observation est
    1/(1/W + phi²) — l'inverse de la variance de log(W), qui vaut 1/W sous
    Poisson plus un terme de sur-dispersion. Sans lui, les rangs à 300 000
    gagnants écraseraient tous les autres.

    `rangs_forces` : restreint le panel à ces rangs. Sert au test de forme
    fonctionnelle, qui compare l'échelle obtenue sur les rangs à faible m et
    à fort m. N'a pas d'usage en production.
    """
    rangs = (list(rangs_forces) if rangs_forces
             else rangs_denses(cfg, tirages, w_min))
    if len(tirages) < MIN_TIRAGES_CALIBRATION or len(rangs) < 2:
        return None
    mb = rangs_mb(cfg)
    paires = PAIRES_POPULARITE if avec_paires else ()
    nb, bb, nf = cfg["n_max"], cfg["bonus_max"], len(paires)
    idx_r = {r: i for i, r in enumerate(rangs)}
    taille = nb + bb + nf + len(rangs) - 1
    off_r = nb + bb + nf
    c_pick2 = math.comb(cfg["pick"], 2)

    XtX = [[0.0] * taille for _ in range(taille)]
    Xty = [0.0] * taille
    grappes, y2 = [], 0.0
    n_lignes = 0

    for t in tirages:
        obs = []
        for r in rangs:
            w = t["gagnants"].get(r)
            if w is None or w < w_min:
                continue
            obs.append((r, math.log(w), 1.0 / (1.0 / w + phi * phi)))
        if len(obs) < 2:                 # il faut ≥2 rangs pour centrer
            continue
        sw = sum(o[2] for o in obs)
        ybar = sum(o[1] * o[2] for o in obs) / sw
        vf = [compter_paires(t["balls"], pr) for _, pr in paires]

        brut = []
        for r, y, w in obs:
            m, b = mb[r]
            col: dict[int, float] = {}
            for j in t["balls"]:
                col[j - 1] = col.get(j - 1, 0.0) + m / cfg["pick"]
            for k in t["bonus"]:
                col[nb + k - 1] = (col.get(nb + k - 1, 0.0)
                                   + b / cfg["bonus_pick"])
            pf = math.comb(int(round(m)), 2) / c_pick2 if m >= 2 else 0.0
            if pf:
                for i, v in enumerate(vf):
                    if v:
                        col[nb + bb + i] = v * pf
            if idx_r[r] > 0:
                col[off_r + idx_r[r] - 1] = 1.0
            brut.append((col, y, w))

        moy: dict[int, float] = {}
        for col, _, w in brut:
            for a, v in col.items():
                moy[a] = moy.get(a, 0.0) + v * w / sw
        lignes = []
        for col, y, w in brut:
            cc = {a: col.get(a, 0.0) - mv for a, mv in moy.items()
                  if abs(col.get(a, 0.0) - mv) > 1e-14}
            yy = y - ybar
            lignes.append((cc, yy, w))
            n_lignes += 1
            y2 += w * yy * yy
            items = list(cc.items())
            for a, va in items:
                Xty[a] += w * va * yy
                for b_, vb in items:
                    if b_ >= a:
                        XtX[a][b_] += w * va * vb
        grappes.append(lignes)

    if len(grappes) < MIN_TIRAGES_CALIBRATION:
        return None
    for a in range(taille):
        for b_ in range(a):
            XtX[a][b_] = XtX[b_][a]

    R = [row[:] for row in XtX]
    for a in range(nb + bb + nf):
        R[a][a] += lam
    try:
        Rinv = _inverser(R)
    except ValueError:
        return None
    beta = [sum(Rinv[i][j] * Xty[j] for j in range(taille))
            for i in range(taille)]

    # --- SE clusterisées par tirage ---------------------------------------
    # Les rangs d'un même tirage partagent le même choc de participation
    # résiduel : les traiter comme indépendants diviserait les SE par ~2 et
    # gonflerait tous les t d'autant.
    pain = [[0.0] * taille for _ in range(taille)]
    sse = 0.0
    for lignes in grappes:
        s: dict[int, float] = {}
        for cc, yy, w in lignes:
            e = yy - sum(beta[a] * v for a, v in cc.items())
            sse += w * e * e
            for a, v in cc.items():
                s[a] = s.get(a, 0.0) + w * v * e
        items = list(s.items())
        for a, va in items:
            for b_, vb in items:
                pain[a][b_] += va * vb
    nc = len(grappes)
    corr = nc / max(nc - 1, 1)
    tmp = [[corr * sum(Rinv[i][k] * pain[k][j] for k in range(taille))
            for j in range(taille)] for i in range(taille)]
    V = [[sum(tmp[i][k] * Rinv[k][j] for k in range(taille))
          for j in range(taille)] for i in range(taille)]
    se = [math.sqrt(max(V[i][i], 0.0)) for i in range(taille)]

    # --- R² partiel : ce que gamma/delta/theta ajoutent aux seuls effets de
    # rang. Le R² brut serait ~0,999 et ne dirait rien : l'écart entre le
    # rang 4 (385 gagnants) et le rang 9 (368 857) écrase tout.
    pr = len(rangs) - 1
    sse_ref = y2
    if pr > 0:
        sub = [[XtX[off_r + i][off_r + j] for j in range(pr)]
               for i in range(pr)]
        sy = [Xty[off_r + i] for i in range(pr)]
        try:
            sinv = _inverser(sub)
            br = [sum(sinv[i][j] * sy[j] for j in range(pr)) for i in range(pr)]
            sse_ref = y2 - sum(br[i] * sy[i] for i in range(pr))
        except ValueError:
            pass
    r2 = 1 - sse / sse_ref if sse_ref > 0 else 0.0

    def centre_et_retrecit(vals, ses):
        """Centrage (seuls les écarts relatifs ont un sens : Σx = pick est
        constant, donc le niveau est absorbé par les effets de rang) puis
        rétrécissement empirique de Bayes vers 0.

        Le rétrécissement corrige le sur-ajustement : gamma est estimé avec
        du bruit, l'employer brut sur un tirage futur sur-pondère les écarts.
        Facteur = var_signal / (var_signal + se²), soit la part de la variance
        observée qui n'est pas du bruit d'échantillonnage. Mesuré hors
        échantillon : pente 0,97 (Loto) après rétrécissement.
        """
        moy_v = statistics.mean(vals)
        c = [v - moy_v for v in vals]
        var_obs = statistics.pvariance(c) if len(c) > 1 else 0.0
        bruit = statistics.mean(s * s for s in ses)
        var_signal = max(var_obs - bruit, 1e-9)
        r = [v * var_signal / (var_signal + s * s)
             for v, s in zip(c, ses, strict=True)]
        # Le facteur de rétrécissement diffère d'un numéro à l'autre : il
        # décentre donc légèrement. On recentre APRÈS, sans quoi pop_rel
        # d'une grille moyenne ne vaudrait plus exactement 1.
        moy_r = statistics.mean(r)
        return [v - moy_r for v in r]

    g = centre_et_retrecit(beta[:nb], se[:nb])
    d = centre_et_retrecit(beta[nb:nb + bb], se[nb:nb + bb])
    gamma = {n: g[n - 1] for n in nums(cfg)}
    delta = {n: d[n - 1] for n in bonus_nums(cfg)}
    theta = {nom: beta[nb + bb + i] for i, (nom, _) in enumerate(paires)}
    se_theta = {nom: se[nb + bb + i] for i, (nom, _) in enumerate(paires)}
    for nom, _ in PAIRES_POPULARITE:          # absentes = sans effet
        theta.setdefault(nom, 0.0)
        se_theta.setdefault(nom, float("inf"))
    ordre = sorted(nums(cfg), key=lambda n: -gamma[n])
    table_paires = _poids_par_paire(theta, cfg["n_max"])
    log_norm_nums, log_norm_bonus = _log_normalisation(
        cfg, gamma, table_paires, delta)
    ts = [abs(gamma[n]) / se[n - 1] for n in nums(cfg) if se[n - 1] > 0]
    return {
        "gamma": gamma, "delta": delta, "theta": theta,
        "table_paires": table_paires,
        "log_norm_nums": log_norm_nums,
        "log_norm": log_norm_nums + log_norm_bonus,
        "se_gamma": {n: se[n - 1] for n in nums(cfg)},
        "se_delta": {n: se[nb + n - 1] for n in bonus_nums(cfg)},
        "se_theta": se_theta,
        "r2": round(r2, 4),
        "n_tirages": nc,
        "n_lignes": n_lignes,
        "rangs": rangs,
        "t_median": round(statistics.median(ts), 2) if ts else 0.0,
        "n_significatifs": sum(1 for v in ts if v > 1.96),
        "top_surjoues": ordre[:6],
        "top_delaisses": ordre[-6:][::-1],
    }


def scores_anti(cfg, calib) -> tuple[dict[int, float], str]:
    """Score anti-partage par numéro : l'opposé de la log-popularité mesurée.

    v2.3 mélangeait 70 % d'empirique et 30 % d'heuristique. Le mélange est
    supprimé : l'heuristique est un prior de littérature, mesurablement faux
    là où il compte (cf. `_pop_bonus_heuristique`), et le rétrécissement
    empirique de Bayes appliqué à gamma joue déjà le rôle de régularisation —
    avec un dosage estimé, pas choisi.
    """
    if not calib:
        return (normaliser({n: -_pop_heuristique(cfg, n) for n in nums(cfg)}),
                "heuristique (littérature) — historique trop court")
    return (normaliser({n: -calib["gamma"][n] for n in nums(cfg)}),
            f"panel à effets fixes (|t| médian {calib['t_median']}, "
            f"{calib['n_significatifs']}/{cfg['n_max']} significatifs, "
            f"n={calib['n_tirages']})")


def scores_anti_bonus(cfg, calib) -> dict[int, float]:
    """Idem pour le n° Chance / les Étoiles.

    La table codée en dur classait le n° Chance 1 parmi les plus joués ; la
    mesure le place BON DERNIER (t = −59). On ne devine plus.
    """
    if not calib:
        return normaliser({n: -_pop_bonus_heuristique(cfg, n)
                           for n in bonus_nums(cfg)})
    return normaliser({n: -calib["delta"][n] for n in bonus_nums(cfg)})


def popularite_log(cfg, balls, calib, bonus=()) -> float:
    """Log-popularité RELATIVE de la grille complète, à l'échelle du rang 1.

    C'est Σ gamma sur les numéros + Σ theta sur les co-occurrences (+ delta
    sur le bonus s'il est fourni). À m = pick, les deux facteurs
    combinatoires valent 1 : le partage du JACKPOT se lit directement ici.
    """
    if not calib:
        logs = {n: math.log(_pop_heuristique(cfg, n)) for n in nums(cfg)}
        m = statistics.mean(logs.values())
        return sum(logs[b] - m for b in balls)
    s = sum(calib["gamma"][b] for b in balls)
    s += somme_paires(balls, calib["table_paires"])
    for k in bonus:
        s += calib["delta"].get(k, 0.0)
    # Ramène la grille MOYENNE à un multiplicateur de 1 (cf.
    # `_log_normalisation`). Sans bonus fourni, on ne retranche que la part
    # « numéros » : retirer aussi la part bonus fausserait le niveau.
    s -= calib["log_norm"] if bonus else calib["log_norm_nums"]
    return s


def pop_rel_grille(cfg, balls, calib, bonus=()) -> float:
    """Multiplicateur de partage de la grille, borné pour l'EV.

    ATTENTION au sens : ce nombre multiplie le nombre ATTENDU de co-gagnants
    au jackpot (cf. `ev_grille`). La v2.3 y injectait des coefficients estimés
    sur le total des gagnants, dominé par les petits rangs : ils valaient
    ~0,17 fois l'élasticité du rang 1 et sous-corrigeaient le partage d'autant.
    """
    return max(0.05, min(20.0, math.exp(popularite_log(cfg, balls, calib,
                                                       bonus))))


# ==============================================================================
# 5. SCORES BONUS (Chance / Étoiles)
# ==============================================================================

def bonus_scores(cfg, tirages, calib=None):
    valides = [t for t in tirages if len(t["bonus"]) == cfg["bonus_pick"]]
    freq, dernier = Counter(), dict.fromkeys(bonus_nums(cfg), -1)
    ewma = dict.fromkeys(bonus_nums(cfg), 0.0)
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
        "anti": scores_anti_bonus(cfg, calib),
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


POIDS_ANTI = 0.5          # part de l'anti-partage en mode hybride


def score_final(cfg, folklore, anti, mode, poids_anti=POIDS_ANTI):
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
    """Contraintes de FORME : une grille publiée doit rester plausible à l'œil.

    Elles ont un coût, parce qu'elles travaillent contre l'anti-partage :
    `meme_dizaine` et `consecutifs` ont un theta NÉGATIF, c'est-à-dire que ces
    motifs sont sous-joués, donc rentables. Chaque contrainte a donc été pesée
    séparément (150 000 grilles tirées au sort, meilleure de chaque pool) :

        contrainte          coût en partage (Loto / Euro)   décision
        parité 2-3 pairs           0 % / 0 %                gardée, gratuite
        pas 3 consécutifs        7,0 % / 9,2 %              gardée
        somme dans les bornes   13,7 % / 11,7 %             gardée
        AU MOINS 2 DIZAINES     15,2 % / 23,0 %             RELÂCHÉE (v2.4)

    Le seuil des dizaines était à 3 et coûtait le plus cher des quatre, pour
    le moins de plausibilité gagnée : la meilleure grille qu'il interdisait
    est 4-31-32-36-37, qui n'a rien d'étrange. Il passe à 2. Les trois autres
    sont conservées — 3 numéros qui se suivent ou une somme extrême sautent
    aux yeux, et la parité ne coûte rien.
    """
    s = sum(balls)
    if not (cts["somme_min"] <= s <= cts["somme_max"]):
        return False
    pairs = sum(1 for b in balls if b % 2 == 0)
    if pairs not in (2, 3):
        return False
    if len({(b - 1) // 10 for b in balls}) < 2:
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


def echelle_paires(cfg, calib, mode: str) -> float:
    """Convertit une log-popularité de grille en points de score de grille.

    Le score d'une grille est la MOYENNE des scores normalisés de ses numéros.
    `normaliser` étale les gamma sur [0, 100], donc une variation de 1 en
    Σ gamma déplace cette moyenne de 100/(étendue × pick) points. Les
    co-occurrences vivent dans la même unité (des log-popularités) : c'est
    donc le facteur de conversion exact, et non un poids à choisir.

    La v2.3 utilisait un « 6.0 » arbitraire devant des pénalités elles-mêmes
    arbitraires. Ici les deux bouts sont mesurés.
    """
    if not calib or mode == "pronostic":
        return 0.0
    vals = list(calib["gamma"].values())
    etendue = max(vals) - min(vals)
    if etendue <= 0:
        return 0.0
    poids_mode = 1.0 if mode == "anti" else POIDS_ANTI
    return poids_mode * 100.0 / (etendue * cfg["pick"])


def penalite_paires(cfg, balls, calib, echelle: float) -> float:
    """Points de score à retrancher pour les co-occurrences de la grille."""
    if not echelle or not calib:
        return 0.0
    return echelle * somme_paires(balls, calib["table_paires"])


def generer_grilles(cfg, scores, sb, tirages, mode, n_grilles, rng, calib,
                    iters: int = 30000):
    cts = contraintes_historiques(tirages)
    dist_d = distribution_deltas(cfg, tirages)
    univers = list(nums(cfg))
    poids = [max(scores[n], 1.0) ** 2 for n in univers]
    ech = echelle_paires(cfg, calib, mode)
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
            g -= penalite_paires(cfg, balls, calib, ech)
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
        comp = composantes_popularite(cfg, balls, calib)
        grilles.append({"numeros": list(balls), "bonus": bons,
                        "score": round(g, 1),
                        "pop_rel": round(pop_rel_grille(cfg, balls, calib), 3),
                        # v2.8 — les composantes séparément (marginal, paires
                        # internes, paires croisées) : elles ne portent pas la
                        # même charge vers un rang donné
                        "pop_comp": [round(v, 4) for v in comp]})
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
    ev_fixe = round(statistics.mean(ev_fixes), 4) if ev_fixes else None
    return {
        "n_est": round(statistics.median(n_ests)),
        "ev_fixe": ev_fixe,
        # Ce que récupère VRAIMENT un joueur d'un ticket par tirage, en part
        # du prix payé. À ne pas confondre avec le TRJ du jeu (~50 %), qui
        # inclut un jackpot que ce joueur ne touchera jamais.
        #
        # Nom explicite (v2.4) : cette valeur porte sur les 160 derniers
        # tirages, alors que `verdicts.trj.trj_hors_jackpot` porte sur tout
        # l'historique. Deux chiffres proches sous le même nom dans le même
        # export, c'est un piège pour qui consomme le contrat.
        "trj_hors_jackpot_recent": (round(ev_fixe / cfg["prix"], 4)
                                    if ev_fixe else None),
        "p_jackpot_inv": proba_jackpot(cfg),
        "prix": cfg["prix"],
    }


def decomposition_trj(cfg, tirages):
    """Décompose le taux de retour joueur à partir des rapports FDJ réels.

    Méthode, indépendante des probabilités théoriques :
        TRJ = Σ(gagnants_k × rapport_k) / (N_est × prix)
    avec N_est = total_gagnants / P(gagner quelque chose). Le même calcul
    privé du rang 1 donne ce qui redescend réellement vers les joueurs
    ordinaires.

    Sur le Loto, le total retombe sur les ~50 % annoncés par la FDJ — ce qui
    valide la mesure — tandis que la version hors rang 1 tombe à ~35 %. La
    différence n'est pas une perte : c'est la part de la mise qui alimente
    un jackpot dont l'espérance, pour un joueur d'un ticket, est nulle en
    pratique.

    Note EuroMillions : les gagnants comptés sont les FRANÇAIS, alors que le
    jackpot est européen. Les rares rangs 1 français rendent `trj_total`
    volatil et plutôt sous-estimé ; `trj_hors_jackpot`, lui, reste solide.
    """
    mises = paye_tot = paye_hors = 0.0
    n = 0
    for t in tirages:
        total_gagnants = sum(t["gagnants"].values())
        if total_gagnants <= 0 or not t["rapports"]:
            continue
        n += 1
        # Prix DE L'ÉPOQUE : le Loto valait 2,00 € avant novembre 2019, et
        # 417 des 1473 tirages sont concernés. Facturer le passé au tarif
        # d'aujourd'hui gonfle les mises et sous-estime le TRJ de 1,3 point —
        # exactement le défaut corrigé dans la rétro-simulation, qui
        # subsistait ici.
        mises += (total_gagnants / cfg["p_any_win"]) * prix_du_tirage(
            cfg, t["date"])
        for rang, rapport in t["rapports"].items():
            montant = t["gagnants"].get(rang, 0) * rapport
            paye_tot += montant
            if rang >= 2:
                paye_hors += montant
    if not mises:
        return None
    return {
        "n_tirages": n,
        "trj_total": round(paye_tot / mises, 4),
        "trj_hors_jackpot": round(paye_hors / mises, 4),
        "part_jackpot": round(1 - paye_hors / paye_tot, 4),
    }


# ==============================================================================
# 7bis. ÉLASTICITÉ DES RAPPORTS — le partage joue à TOUS LES RANGS
# ==============================================================================
#
# Jusqu'en v2.7, le moteur ne corrigeait le partage qu'au RANG 1. Erreur de
# cadrage, pas de calcul : dans un jeu de tirage français, TOUS les rangs sont
# pari-mutuels — une part fixe de la cagnotte divisée par le nombre de
# gagnants. Jouer des numéros délaissés augmente donc le rapport à chaque rang,
# pas seulement au jackpot.
#
# L'écart est décisif pour qui joue :
#   · au rang 1, le gain se réalise une fois tous les 122 000 ans ;
#   · aux rangs courants, une douzaine de fois par an.
# Le moteur annonçait donc son seul levier réel sous une forme invérifiable,
# et sous-estimait sa valeur d'un facteur ~7.
#
# Ce qu'on mesure ici, rang par rang :
#
#     log(rapport_r) = a_r + beta_r · P + c_r · log(W_affluence)
#
#   · P = log-popularité de la combinaison SORTIE. C'est la FDJ qui la tire au
#     sort : la variable explicative est randomisée, donc aucun facteur caché
#     ne peut expliquer le résultat. Même argument que pour la calibration —
#     une expérience randomisée dont la FDJ publie les résultats, pas une
#     corrélation trouvée après coup.
#   · W_affluence = gagnants d'un rang insensible aux boules (`rang_affluence`).
#     Il absorbe le jackpot, la saison, la publicité — tout ce qui fait varier
#     le nombre de joueurs sans rien devoir aux numéros sortis.
#
# CE QUE CETTE SECTION PROUVE, ET CE QU'ELLE NE PROUVE PAS
# --------------------------------------------------------
# À dire avant les résultats, parce qu'un audit hostile l'a établi et que le
# taire reviendrait à vendre une preuve qu'on n'a pas.
#
# Au LOTO, le rapport est l'inverse quasi exact du nombre de gagnants :
# régressés sur les mêmes variables, beta_rapport / beta_gagnants vaut 0,95 à
# 0,98 sur le régime courant. Or le nombre de gagnants est PRÉCISÉMENT ce que
# `calibration_empirique` ajuste, avec la charge m/pick imposée. Cette section
# ne fournit donc pas, au Loto, une confirmation indépendante de la
# calibration : elle la traduit en euros, rang par rang. C'est utile — c'est
# même tout l'objet — mais ce n'est pas une seconde preuve.
#
# À l'EUROMILLIONS le ratio tombe à 0,68-0,82, et pour une raison connue : les
# gagnants publiés sont FRANÇAIS alors que le rapport est fixé par le pool
# EUROPÉEN. Il y a là un contenu que la calibration ne peut pas voir.
#
# La preuve indépendante, elle, est ailleurs : c'est `backtest_partage`, qui
# règle de vraies grilles sur de vrais tirages sans jamais toucher à ce modèle.
#
# Une prédiction signée survit, et elle est réfutable : beta_r < 0, avec
# |beta_r| CROISSANT selon le m du rang. Elle est cohérente avec la forme
# imposée à la calibration, donc c'est un test d'ajustement, pas une découverte.
# Le PLACEBO, lui, est une permutation (voir `placebo_permutation`) : la mesure
# entière rejouée sur des combinaisons mélangées entre tirages. C'est le seul
# contrôle de cette section qui puisse échouer.
#
# LE PIÈGE, ET POURQUOI beta NE S'APPLIQUE PAS TEL QUEL
# -----------------------------------------------------
# beta_r répond à la popularité de la combinaison SORTIE. Ce n'est pas la
# popularité de TA grille, et confondre les deux surestime le levier d'un
# facteur pick/m — au rang « 2 numéros » du Loto, un facteur 2,5.
#
# Quand ta grille touche le rang r, la combinaison sortie contient m de tes
# numéros et (pick − m) numéros qui ne sont pas à toi. Sa popularité ne te doit
# donc qu'une fraction de la tienne :
#
#     E[P_sorti | ta grille touche r] − E[P_sorti]
#         = (m/pick) · (marginal de ta grille − moyenne)
#         + (C(m,2)/C(pick,2)) · (co-occurrences de ta grille − moyenne)
#
# Les deux composantes ne portent PAS la même charge combinatoire — c'est la
# décomposition que tools/zones_faibles.py établit déjà. D'où
# `composantes_popularite`, et des charges séparées par rang.
#
# Le cas m = pick vérifie la formule : au rang « 5 numéros », la combinaison
# sortie EST ta grille, les deux charges valent 1, et l'on retombe exactement
# sur le traitement du jackpot. C'est le contrôle qui dit que l'atténuation
# est la bonne.
#
# `backtest_partage` mesure ensuite le résultat SANS ce modèle, en réglant de
# vraies grilles sur de vrais tirages. Si les deux ne concordent pas, c'est le
# backtest qui a raison.

MIN_TIRAGES_ELASTICITE = 200
MIN_OBS_RANG_ELASTICITE = 60
# En deçà, un rang du backtest apparié ne dit rien : il n'est ni publié dans le
# tableau, ni compté dans la surcote globale. Le premier jet l'écartait du
# tableau SANS l'écarter du total — un rang touché deux fois portait alors
# 30 % du chiffre de tête, invisible.
MIN_TIRAGES_RANG_PARTAGE = 5


def _mco(colonnes, y):
    """MCO avec constante. Rend (coefs hors constante, écarts-types)."""
    p = len(colonnes) + 1
    n = len(y)
    if n <= p:
        return None
    X = [[1.0] + [c[i] for c in colonnes] for i in range(n)]
    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(p)]
           for a in range(p)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(p)]
    try:
        inv = _inverser(XtX)
    except ValueError:
        return None
    beta = [sum(inv[a][b] * Xty[b] for b in range(p)) for a in range(p)]
    sse = sum((y[i] - sum(beta[a] * X[i][a] for a in range(p))) ** 2
              for i in range(n))
    s2 = sse / (n - p)
    return ([beta[a] for a in range(1, p)],
            [math.sqrt(max(s2 * inv[a][a], 0.0)) for a in range(1, p)])


def rang_affluence(cfg, tirages):
    """Rang témoin d'affluence : le rang dense au m effectif le plus faible.

    Son nombre de gagnants suit la foule du soir, mais ne doit presque rien à
    la popularité des boules sorties — c'est ce qui en fait un contrôle et non
    un second traitement. Loto : rang 9 (« Chance seule », m = 0,38).
    EuroMillions : rang 11 (« 1 + 2 étoiles », m = 1).

    Le contrôle n'est pas parfaitement neutre (m > 0). Une version antérieure
    de ce commentaire affirmait que son imperfection « tire beta vers zéro,
    donc joue dans le sens prudent ». C'était faux en signe : le biais vaut
    −lambda·eta où lambda est l'effet propre du témoin sur le rapport étudié et
    eta son élasticité résiduelle à la popularité. Mesuré au Loto, les deux
    sont négatifs, donc le biais AGRANDIT |beta| au lieu de le réduire. Son
    amplitude reste petite — 0,5 % à 4 % de beta selon le rang, contre 2,2 %
    à l'EuroMillions où le signe est bien celui qu'on croyait. On ne s'en sert
    donc plus comme argument de prudence : on le chiffre et on passe.
    """
    denses = rangs_denses(cfg, tirages)
    if not denses:
        return None
    mb = rangs_mb(cfg)
    return min(denses, key=lambda r: (mb[r][0], r))


def composantes_popularite(cfg, balls, calib):
    """Décompose la popularité d'une grille en trois quantités qui ne se
    propagent PAS de la même façon vers un rang donné :

      · `marg`      : Σ gamma sur les numéros de la grille (normalisé) ;
      · `pair_in`   : Σ theta sur les paires INTERNES à la grille ;
      · `pair_cross`: Σ theta sur les paires grille × hors-grille.

    Les mélanger donnerait une charge hybride ininterprétable — c'est la même
    séparation que celle établie par tools/zones_faibles.py, poussée d'un cran
    parce que le conditionnement « j'ai trouvé m numéros » fait intervenir le
    complémentaire de la grille (cf. `charges_combinatoires`).
    """
    if not calib:
        return (popularite_log(cfg, balls, calib), 0.0, 0.0)
    table = calib["table_paires"]
    pair_in = somme_paires(balls, table)
    # Σ_{i ∈ G} Σ_{j ≠ i} theta_ij compte deux fois les paires internes
    total_lignes = sum(sum(table[b]) for b in balls)
    pair_cross = total_lignes - 2.0 * pair_in
    return (popularite_log(cfg, balls, calib) - pair_in, pair_in, pair_cross)


def charges_combinatoires(cfg, m: float) -> tuple[float, float, float]:
    """Fraction de la popularité d'une grille que la combinaison sortie porte
    encore quand on en a trouvé `m` numéros : (marginal, paires internes,
    paires croisées).

    LA DÉRIVATION, ET LE TERME QUI MANQUAIT
    ---------------------------------------
    Sachant |D ∩ G| = m, l'intersection est un m-sous-ensemble uniforme de la
    grille G — d'où le facteur m/pick, évident. Mais le RESTE du tirage,
    D \\ G, n'est pas tiré de la population entière : il est tiré du
    COMPLÉMENTAIRE de G. Comme gamma est centré sur l'univers
    (Σ_univers gamma = 0, vérifié à 2·10⁻¹⁶ près), la somme des gamma hors
    grille vaut −Σ_G gamma, et ces (pick − m) numéros apportent donc un terme
    NÉGATIF proportionnel à la popularité de la grille :

        E[Σ_D gamma | m] = [ m/pick − (pick − m)/(n_max − pick) ] · Σ_G gamma

    La v2.8 initiale n'écrivait que `m/pick`. L'erreur atteint un facteur 1,83
    au rang à 1 bon numéro, et INVERSE le signe en dessous de m = 1. Contrôle
    Monte-Carlo sur 300 000 tirages (grille anti du Loto, Σ gamma = −0,9837) :
    à m = 1, mesuré −0,1076, formule ci-dessus −0,1073, ancienne formule
    −0,1967.

    Le piège, et pourquoi il a tenu : le terme oublié s'annule EXACTEMENT en
    m = pick. Le « contrôle » qui vérifiait que les charges valent 1 quand la
    combinaison sortie est la grille ne pouvait donc rien détecter. Il est
    remplacé par une vérification Monte-Carlo à tous les m.

    Les paires suivent la même logique, en trois blocs au lieu d'un : paires
    internes à D ∩ G, paires internes à D \\ G, et paires croisées entre les
    deux. Seul le premier était retenu. Comme la somme des theta sur TOUTES
    les paires de l'univers est la même quelle que soit la grille, le bloc
    « hors grille » se réécrit en fonction des deux autres et disparaît des
    inconnues : il ne laisse qu'une soustraction sur leurs charges.
    """
    n, k = cfg["n_max"], cfg["pick"]
    dehors = n - k
    if dehors <= 1 or k <= 1:
        return (m / k if k else 0.0, 1.0, 0.0)
    marg = m / k - (k - m) / dehors
    c_k2 = k * (k - 1) / 2
    c_out2 = dehors * (dehors - 1) / 2
    a = max(0.0, m * (m - 1) / 2) / c_k2                 # paires de D ∩ G
    b = (m * (k - m)) / (k * dehors)                     # paires croisées
    c = max(0.0, (k - m) * (k - m - 1) / 2) / c_out2     # paires de D \ G
    return (marg, a - c, b - c)


def debut_pari_mutuel(tirages, rang: int, run_min: int = 30) -> int:
    """Index du premier tirage à partir duquel `rang` paie un montant VARIABLE.

    LE PIÈGE QUE CETTE FONCTION DÉSAMORCE
    -------------------------------------
    Avant le 4 novembre 2019, les rangs 2 à 9 du Loto payaient un montant
    FIXE. Mesuré sur les archives : sur les 417 tirages antérieurs, chacun de
    ces rangs ne prend qu'UNE SEULE valeur distincte — 500 € au rang 4, 20 €
    au rang 6, 5 € au rang 8. Aucun de ces tirages ne peut porter la moindre
    élasticité : le rapport ne dépendait pas des gagnants.

    Les garder dans l'estimation, c'est ajouter 28 % d'observations à pente
    NULLE PAR CONSTRUCTION, donc raboter toutes les pentes de 28 %. C'est
    exactement le piège que ce moteur a déjà corrigé deux fois sous une autre
    forme — « le TRJ facturait le passé au tarif d'aujourd'hui ».

    Détection sans date codée en dur, pour que le jour où la FDJ rebasculera
    un rang en prix fixe le moteur s'en aperçoive seul : on remonte le temps
    depuis le tirage le plus récent et l'on coupe dès qu'on rencontre `run_min`
    tirages consécutifs au rapport rigoureusement identique. Aucun régime
    pari-mutuel ne produit une telle plage ; un prix fixe ne produit que ça.

    Rend `len(tirages)` si le rang est à prix fixe jusqu'au bout — auquel cas
    il n'y a rien à estimer, et c'est un fait sur le jeu, pas un échec.
    """
    idx = [i for i, t in enumerate(tirages)
           if t["rapports"].get(rang) and t["rapports"][rang] > 0]
    if len(idx) < run_min:
        return len(tirages)
    course = 1
    for pos in range(len(idx) - 1, 0, -1):
        a, b = tirages[idx[pos]], tirages[idx[pos - 1]]
        if a["rapports"][rang] == b["rapports"][rang]:
            course += 1
            if course >= run_min:
                return idx[pos + run_min - 2] + 1
        else:
            course = 1
    return 0


def elasticite_rangs(cfg, tirages, calib):
    """Mesure beta_r pour chaque rang, plus le domaine de validité du modèle.

    Rend aussi `parts` : la fraction des gains hors jackpot que chaque rang
    apporte réellement, reconstruite depuis les gagnants et les rapports FDJ.
    C'est elle qui pondère les beta quand on corrige l'EV — un rang au beta
    spectaculaire mais qui ne pèse rien ne doit pas tirer le résultat.

    Les rangs sans estimation exploitable gardent beta = 0 (neutre) tout en
    conservant leur part : au Loto, le rang 9 paie 2,20 € depuis toujours et
    représente 26 % des gains hors jackpot. Cette part-là n'est améliorable par
    aucune stratégie, et la diluer dans le facteur est exactement ce qu'il faut
    faire.

    Chaque rang est estimé sur SON régime pari-mutuel seulement — voir
    `debut_pari_mutuel`. Les rangs n'ont pas tous basculé au même moment, d'où
    une fenêtre par rang plutôt qu'une date commune.
    """
    if not calib or len(tirages) < MIN_TIRAGES_ELASTICITE:
        return None
    ra = rang_affluence(cfg, tirages)
    if ra is None:
        return None
    mb = rangs_mb(cfg)
    comps = {i: composantes_popularite(cfg, t["balls"], calib)
             for i, t in enumerate(tirages)}
    # Le RÉGRESSEUR est la log-popularité de la combinaison sortie, c'est-à-dire
    # marginal + paires internes — exactement `popularite_log`. Les paires
    # CROISÉES n'ont pas leur place ici : elles n'existent que dans la
    # décomposition d'une grille de joueur face au reste de l'univers, pas dans
    # la popularité d'un tirage. Les y ajouter change la variable expliquée en
    # cours de route et fait s'évanouir tout l'effet.
    pops = {i: c[0] + c[1] for i, c in comps.items()}
    p_ref = statistics.mean(pops.values())
    refs = tuple(statistics.mean(c[j] for c in comps.values())
                 for j in range(3))

    def regresser(indices, valeurs_p, rang):
        """MCO du log-rapport de `rang` sur la popularité et l'affluence."""
        ys, xs_pop, xs_aff = [], [], []
        for i in indices:
            t = tirages[i]
            v = t["rapports"].get(rang)
            aff = t["gagnants"].get(ra, 0)
            if v and v > 0 and aff > 0:
                ys.append(math.log(v))
                xs_pop.append(valeurs_p[i])
                xs_aff.append(math.log(aff))
        if len(ys) < MIN_OBS_RANG_ELASTICITE or statistics.pstdev(ys) < 1e-12:
            return None, len(ys)
        return _mco([xs_pop, xs_aff], ys), len(ys)

    beta, ses, ts, nobs, depuis = {}, {}, {}, {}, {}
    for r in sorted(mb):
        d = debut_pari_mutuel(tirages, r)
        depuis[r] = d
        out, n = regresser(range(d, len(tirages)), pops, r)
        nobs[r] = n
        if out is None:
            beta[r], ses[r], ts[r] = 0.0, None, None
            continue
        (b_pop, _), (se_pop, _) = out
        beta[r] = b_pop
        ses[r] = se_pop
        ts[r] = b_pop / se_pop if se_pop > 1e-12 else 0.0

    # Part d'EV réellement apportée par chaque rang, hors jackpot. Mesurée sur
    # le régime courant : les parts d'un régime de prix fixes ne décrivent plus
    # le jeu auquel on joue ce soir.
    # Le rang 1 est hors parts : le laisser entrer dans ce minimum le ramènerait
    # toujours à 0 et annulerait la stratification.
    depart_parts = min((d for r, d in depuis.items()
                        if r != 1 and d < len(tirages) and beta.get(r)),
                       default=0)
    paye = dict.fromkeys(mb, 0.0)
    for t in tirages[depart_parts:]:
        for r, rap in t["rapports"].items():
            if r in paye and r != 1:
                paye[r] += (t["gagnants"].get(r, 0) or 0) * rap
    total = sum(paye.values())
    parts = ({r: paye[r] / total for r in paye if r != 1} if total > 0
             else {r: 0.0 for r in paye if r != 1})

    # Domaine observé : au-delà, le modèle extrapole et on doit le dire
    ecarts = sorted(p - p_ref for p in pops.values())
    plancher = ecarts[max(len(ecarts) // 100 - 1, 0)]
    plafond = ecarts[min(99 * len(ecarts) // 100, len(ecarts) - 1)]

    return {
        "rang_affluence": ra,
        "p_reference": p_ref,
        "refs": refs,
        "plancher": plancher,
        "plafond": plafond,
        "beta": beta,
        "se": ses,
        "t": ts,
        "n_obs": nobs,
        "depuis": depuis,
        "parts": parts,
        "charges": {r: charges_combinatoires(cfg, mb[r][0]) for r in mb},
        "placebo": placebo_permutation(cfg, tirages, calib, ra, depuis),
        "n_tirages": len(tirages),
        "depart_parts": depart_parts,
    }


def placebo_permutation(cfg, tirages, calib, ra, depuis, n_essais: int = 6,
                        graine: int = 20260808):
    """Le VRAI placebo : la même mesure sur des tirages dont on a mélangé les
    combinaisons entre eux.

    Ce qui a rendu ce remplacement nécessaire
    -----------------------------------------
    Le placebo précédent prenait le rang à m ≈ 0 et vérifiait qu'il sortait à
    zéro. Au Loto, ce rang paie 2,20 € — UNE seule valeur distincte sur 1474
    tirages. Son beta était donc nul par identité comptable, l'assertion ne
    pouvait pas échouer, et une tautologie était publiée comme une réfutation.

    Ici on permute les combinaisons entre tirages : chaque tirage garde ses
    rapports, ses gagnants et son affluence, mais reçoit les boules d'un autre
    soir. Toute relation entre popularité et rapport est détruite, la loi
    marginale de la popularité est intacte (contrairement à un bruit gaussien),
    et la méthode complète est rejouée. Ce qui survit mesure ce que la méthode
    fabrique toute seule.

    Rend le |t| maximal et le nombre de coefficients qui franchiraient 1,96 —
    zéro attendu, et un chiffre qui PEUT ne pas l'être.
    """
    rng = random.Random(graine)
    mb = rangs_mb(cfg)
    t_max, n_signif, n_coefs = 0.0, 0, 0
    for _ in range(n_essais):
        melange = [t["balls"] for t in tirages]
        rng.shuffle(melange)
        faux = {i: sum(composantes_popularite(cfg, b, calib))
                for i, b in enumerate(melange)}
        for r in sorted(mb):
            ys, xs, aff = [], [], []
            for i in range(depuis.get(r, 0), len(tirages)):
                t = tirages[i]
                v = t["rapports"].get(r)
                a = t["gagnants"].get(ra, 0)
                if v and v > 0 and a > 0:
                    ys.append(math.log(v))
                    xs.append(faux[i])
                    aff.append(math.log(a))
            if len(ys) < MIN_OBS_RANG_ELASTICITE:
                continue
            if statistics.pstdev(ys) < 1e-12:
                continue
            out = _mco([xs, aff], ys)
            if not out:
                continue
            (b, _), (se, _) = out
            n_coefs += 1
            if se > 1e-12:
                t_stat = abs(b / se)
                t_max = max(t_max, t_stat)
                n_signif += t_stat > 1.96
    return {"n_essais": n_essais, "n_coefficients": n_coefs,
            "n_significatifs": n_signif, "t_max": round(t_max, 2),
            "methode": "combinaisons permutées entre tirages"}


def facteur_partage(elast, composantes, prudent: bool = True) -> float:
    """Multiplicateur des gains HORS jackpot d'une grille, à partir de ses
    `composantes` (marginal, co-occurrences) rendues par
    `composantes_popularite`.

    Vaut 1 pour une grille aussi populaire qu'une combinaison quelconque, et
    davantage pour une grille délaissée. Chaque rang reçoit l'écart ATTÉNUÉ
    par ses charges combinatoires — c'est ce qui distingue la popularité de ta
    grille de celle du tirage (voir le commentaire de section).

    `prudent` bride l'écart total au 1er centile des combinaisons RÉELLEMENT
    sorties. Au-delà, le modèle extrapole hors du domaine où il a été vérifié,
    et les grilles extrêmes du mode « anti » sont précisément dans ce cas. On
    préfère annoncer moins que promettre ce qui n'a pas été mesuré.

    Réserve assumée : `ev_fixe` est le gain moyen d'une grille RÉELLEMENT
    JOUÉE, donc d'une grille plus populaire que la moyenne, alors que le
    facteur vaut 1 sur une combinaison quelconque. L'ancrage sous-estime donc
    légèrement l'avantage de l'anti-partage face au joueur moyen. On garde le
    sens prudent.
    """
    if (not elast or composantes is None
            or sum(elast["parts"].values()) <= 0.0):
        return 1.0
    ecarts = [c - r for c, r in zip(composantes, elast["refs"], strict=True)]
    # Le domaine observé est celui de la log-popularité d'un TIRAGE, donc
    # marginal + paires internes. C'est sur cette échelle-là qu'on bride.
    total = ecarts[0] + ecarts[1]
    # Le bridage tient aux DEUX bords, et seulement en mode prudent : au-delà
    # du domaine observé le modèle extrapole, quel que soit le côté. Ne brider
    # que le bas rendrait `prudent` et `extrapolé` identiques sur les grilles
    # très jouées — donc muet là où l'écart se lit le mieux.
    borne = None
    if prudent and total < elast["plancher"]:
        borne = elast["plancher"]
    elif prudent and total > elast["plafond"]:
        borne = elast["plafond"]
    if borne is not None and abs(total) > 1e-12:
        k = borne / total
        ecarts = [e * k for e in ecarts]
    f = 0.0
    for r, part in elast["parts"].items():
        charges = elast["charges"].get(r, (1.0, 1.0, 0.0))
        dp = sum(c * e for c, e in zip(charges, ecarts, strict=True))
        # garde-fou : une calibration future pathologique ne doit pas faire
        # tomber le moteur sur un OverflowError au moment d'afficher une EV
        expo = min(max(elast["beta"].get(r, 0.0) * dp, -50.0), 50.0)
        f += part * math.exp(expo)
    return f


def ev_grille(ev_p, jackpot: float, pop_rel: float, elast=None,
              composantes=None) -> dict:
    """EV(grille) = gains courants corrigés du partage
                    + p1·J / (1 + partageurs attendus) − prix.

    v2.8 : les gains courants ne sont plus une constante. Ils sont multipliés
    par `facteur_partage`, qui applique au rapport de CHAQUE rang l'élasticité
    mesurée sur les rapports FDJ, atténuée par les charges combinatoires du
    rang. Sans `elast` ou sans `composantes`, le facteur vaut 1 et l'on
    retombe exactement sur le comportement antérieur — jamais sur une
    approximation silencieuse.

    `ev` retient la version PRUDENTE (écart bridé au domaine observé) ;
    `ev_extrapole` donne la version non bridée, pour que l'écart entre les
    deux reste visible plutôt que tranché en coulisses.
    """
    p1 = 1.0 / ev_p["p_jackpot_inv"]
    partageurs = ev_p["n_est"] * p1 * pop_rel
    comp_jack = p1 * jackpot / (1.0 + partageurs)
    fixe = ev_p["ev_fixe"] or 0.0
    f_pru = facteur_partage(elast, composantes, prudent=True)
    f_bru = facteur_partage(elast, composantes, prudent=False)
    ev = fixe * f_pru + comp_jack - ev_p["prix"]
    return {"ev": round(ev, 4),
            "ev_extrapole": round(fixe * f_bru + comp_jack - ev_p["prix"], 4),
            "comp_jackpot": round(comp_jack, 4),
            "comp_courants": round(fixe * f_pru, 4),
            "facteur_partage": round(f_pru, 4),
            "facteur_partage_extrapole": round(f_bru, 4),
            "partageurs_attendus": round(partageurs, 4),
            "alerte": _alerte_ev(ev)}


def _alerte_ev(ev: float) -> str | None:
    """Avertissement obligatoire quand l'EV calculée devient positive.

    Aux très gros reports, l'arithmétique donne effectivement une espérance
    positive — au Loto, à partir d'environ 26 M€ pour une grille délaissée.
    Publier ce chiffre nu serait un conseil de jeu, et un mauvais, pour deux
    raisons qu'il faut dire dans la même phrase :

      · `n_est` est la participation MÉDIANE des 160 derniers tirages. Or un
        gros jackpot attire massivement plus de joueurs, donc plus de
        partageurs. Le calcul tient la foule pour constante alors qu'elle est
        précisément ce qui explose ces soirs-là : l'EV affichée est un
        majorant, pas une prévision.
      · même exacte, elle reposerait à 100 % sur un événement à 1 sur
        19 068 840. L'espérance est positive, la médiane du joueur reste
        « tu perds tout ». Ce n'est pas une opportunité, c'est une loterie.
    """
    if ev < 0:
        return None
    return ("EV positive sur le papier. Elle suppose la participation "
            "CONSTANTE, alors qu'un gros jackpot attire beaucoup plus de "
            "joueurs — donc plus de partageurs : c'est un majorant, pas une "
            "prévision. Et elle repose entièrement sur un événement à une "
            "chance sur des millions : l'espérance monte, le résultat le plus "
            "probable reste de tout perdre.")


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
    #   L'ordre suit le MONTANT décroissant, pas la rareté. Piège classique :
    #   4+0 est un peu PLUS probable que 3+2 mais nettement MOINS payé
    #   (46,50 € contre 69,30 € de rapport médian sur les tirages FDJ), donc
    #   3+2 = rang 6 et 4+0 = rang 7. Les intervertir ne plante rien : ça paie
    #   silencieusement un 4+0 au tarif d'un 3+2 (+49 %) et l'inverse (−33 %).
    table = {(5, 2): 1, (5, 1): 2, (5, 0): 3, (4, 2): 4, (4, 1): 5,
             (3, 2): 6, (4, 0): 7, (2, 2): 8, (3, 1): 9, (3, 0): 10,
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
        for grs in e["modes"].values():
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

def retro_simulation(cfg, tirages, n_derniers: int = 150, seed: int = 0,
                     iters: int = 30000, retour_grilles: bool = False,
                     n_graines_dispersion: int = 0,
                     iters_dispersion: int = 3000):
    """Rejoue les N derniers tirages en jouant LES GRILLES QUI AURAIENT ÉTÉ
    PUBLIÉES, réglées aux rapports réels du tirage.

    Fidélité exacte au pipeline de publication (v2.4)
    ------------------------------------------------
    Une version antérieure jouait un raccourci — le top-5 des scores, bonus
    déterministe — en affirmant que c'était « largement suffisant pour mesurer
    le ROI réel ». L'audit a montré que c'était faux : ces grilles diffèrent
    des grilles publiées dans 92 à 100 % des cas, et les ROI divergent jusqu'à
    23 points dans les deux sens. Ce n'était pas un biais, mais un chiffre qui
    ne mesurait pas ce qu'il prétendait mesurer.

    On refait donc exactement ce que fait la publication : `calculer_scores`,
    `score_final`, puis `generer_grilles` avec ses 30 000 itérations et ses
    contraintes de forme. Coût mesuré : ~110 s pour 100 tirages et 3 modes —
    négligeable dans le cron, et c'est le prix d'un chiffre qui dit vrai.

    Reste une part d'aléa assumée : `generer_grilles` est une recherche
    stochastique, deux exécutions ne rendent pas la même grille. La graine est
    donc fixée, comme en publication, pour que le résultat soit reproductible.

    `iters` reste réglable pour les tests : ils exercent ainsi le VRAI
    pipeline (contraintes de forme comprises) sans payer les 30 000 tirages
    aléatoires de la publication.

    `n_graines_dispersion` rejoue le mode « anti » avec d'autres graines, à
    stratégie STRICTEMENT identique, pour mesurer ce que le hasard du
    générateur pèse à lui seul. Sans cela le produit affiche un nombre unique
    là où la loi s'étale d'un facteur 25 à 27 : ce n'est pas une mesure, c'est
    un échantillon de taille 1. Les scores étant déjà calculés pour le tirage
    courant, seul le tirage de grilles est refait — d'où `iters_dispersion`
    plus petit, la dispersion n'ayant pas besoin de la même finesse que la
    grille publiée.

    `retour_grilles` ajoute la liste des grilles jouées, tirage par tirage.
    Hors export (le contrat JSON n'en veut pas), mais c'est ce qui rend
    vérifiable la propriété la plus importante de cette fonction : la grille
    jouée au tirage i ne doit dépendre QUE des tirages antérieurs. Sans ce
    point d'observation, une fuite du futur d'un seul cran est indétectable
    de l'extérieur — et une fuite gonfle le ROI affiché.
    """
    debut = max(60, len(tirages) - n_derniers)
    calib, prochaine_calib = None, debut
    res = {m: {"mise": 0.0, "gain": 0.0, "rangs": Counter(),
               "n_gains": 0, "meilleur_gain": 0.0, "meilleur_date": None,
               "gains": [], "tirages": [], "grilles_jouees": []}
           for m in ("hybride", "pronostic", "anti")}
    autres_graines = [0.0] * n_graines_dispersion

    for i in range(debut, len(tirages)):
        passe = tirages[:i]                       # strictement le passé
        if i >= prochaine_calib:
            calib = calibration_empirique(cfg, passe)
            prochaine_calib = i + 25
        t = tirages[i]
        _, folk, anti, _ = calculer_scores(cfg, passe, t["jour"], calib)
        sb = bonus_scores(cfg, passe, calib)
        for mode in res:
            fin = score_final(cfg, folk, anti, mode)
            grilles = generer_grilles(cfg, fin, sb, passe, mode, 1,
                                      random.Random(seed), calib, iters=iters)
            if not grilles:
                continue
            grille = list(grilles[0]["numeros"])
            bons_b = list(grilles[0]["bonus"])
            if retour_grilles:
                res[mode]["grilles_jouees"].append(
                    {"date": t["date"].isoformat(), "numeros": grille,
                     "bonus": bons_b})
            if mode == "anti" and n_graines_dispersion:
                for k in range(n_graines_dispersion):
                    alt = generer_grilles(cfg, fin, sb, passe, mode, 1,
                                          random.Random(seed + 1 + k), calib,
                                          iters=iters_dispersion)
                    if alt:
                        autres_graines[k] += regler_grille(
                            cfg, {"numeros": alt[0]["numeros"],
                                  "bonus": alt[0]["bonus"]}, t)["gain"]
            r = regler_grille(cfg, {"numeros": grille, "bonus": bons_b}, t)
            res[mode]["mise"] += prix_du_tirage(cfg, t["date"])
            res[mode]["gain"] += r["gain"]
            # Chaque tirage rejoué, gagnant OU perdant : la page montre
            # l'historique complet, pas seulement les soirs où ça paie.
            res[mode]["tirages"].append({
                "date": t["date"].isoformat(),
                "grille": list(grille),
                "bonus": list(bons_b),
                "sortis": list(t["balls"]),
                "bonus_sortis": list(t["bonus"]),
                "bons": r["bons"],
                "bonus_ok": r["bonus_ok"],
                "rang": r["rang"],
                "gain": round(r["gain"], 2),
            })
            if r["rang"]:
                res[mode]["rangs"][r["rang"]] += 1
                res[mode]["n_gains"] += 1
                # Détail de chaque gain : sans lui, le total « récupéré » reste
                # un chiffre à croire sur parole. Là, chaque euro est traçable
                # à un tirage, un rang et un rapport FDJ réels.
                res[mode]["gains"].append({
                    "date": t["date"].isoformat(),
                    "grille": list(grille),
                    "bonus": list(bons_b),
                    "sortis": list(t["balls"]),
                    "bons": r["bons"],
                    "bonus_ok": r["bonus_ok"],
                    "rang": r["rang"],
                    "gain": round(r["gain"], 2),
                })
                if r["gain"] > res[mode]["meilleur_gain"]:
                    res[mode]["meilleur_gain"] = r["gain"]
                    res[mode]["meilleur_date"] = t["date"].isoformat()

    dispersion = None
    if n_graines_dispersion >= 5:
        g = sorted(autres_graines)
        dispersion = {
            "n_graines": len(g),
            "p10": round(g[max(int(0.10 * len(g)) - 1, 0)], 2),
            "mediane": round(statistics.median(g), 2),
            "p90": round(g[min(int(0.90 * len(g)), len(g) - 1)], 2),
            "min": round(g[0], 2), "max": round(g[-1], 2),
            "note": ("mêmes scores, mêmes contraintes, seule la graine du "
                     "générateur change"),
        }

    n_sim = len(tirages) - debut
    return {"n_tirages": n_sim, "dispersion_anti": dispersion,
            "note": "grilles réellement générées, réglées aux rapports FDJ",
            "prix": cfg["prix"],
            "modes": {m: {"mise": round(v["mise"], 2),
                          "gain": round(v["gain"], 2),
                          "roi_pct": round(100 * (v["gain"] - v["mise"])
                                           / v["mise"], 1) if v["mise"] else 0,
                          "n_gains": v["n_gains"],
                          "gains": sorted(v["gains"], key=lambda g: -g["gain"]),
                          # du plus récent au plus ancien : l'ordre de lecture
                          # de la page
                          "tirages": sorted(v["tirages"],
                                            key=lambda g: g["date"],
                                            reverse=True),
                          "meilleur_gain": round(v["meilleur_gain"], 2),
                          "meilleur_date": v["meilleur_date"],
                          "rangs": dict(sorted(v["rangs"].items())),
                          # Absent de l'export tant que retour_grilles est
                          # faux : le contrat JSON n'en veut pas.
                          **({"grilles_jouees": v["grilles_jouees"]}
                             if retour_grilles else {})}
                      for m, v in res.items()}}


# ==============================================================================
# 8quater. BACKTEST APPARIÉ DU PARTAGE — le seul levier, enfin mesurable
# ==============================================================================
#
# Pourquoi ne pas se contenter du ROI
# -----------------------------------
# Le ROI d'une rétro-simulation est dominé par la chance : la même stratégie
# rejouée avec une autre graine rend de 27 € à 172 € sur les mêmes 100 tirages
# (mesuré par tools/dispersion_simulation.py). Comparer deux stratégies
# là-dessus revient à comparer deux lancers de dé — et c'est ce que fait tout
# le monde, y compris ce moteur jusqu'en v2.7.
#
# Ce qu'on mesure à la place
# --------------------------
# LE RAPPORT ENCAISSÉ SACHANT QU'ON A TOUCHÉ UN RANG. La séparation est nette :
#
#   · la PROBABILITÉ de toucher ne dépend d'aucune stratégie. Elle est fixée
#     par le tirage, et aucun algorithme ne la déplacera jamais ;
#   · le MONTANT encaissé, lui, dépend de la grille — parce que tous les rangs
#     sont pari-mutuels. C'est là, et seulement là, qu'une stratégie agit.
#
# La puissance statistique change d'échelle : au Loto on touche un rang une
# fois sur ~6, donc ~11 fois par an, contre une fois tous les 122 000 ans pour
# le jackpot. Sur l'historique complet, cela fait des centaines d'événements.
#
# Le test
# -------
# Sous l'hypothèse nulle « la grille n'a aucun effet sur le montant », les
# tirages où une stratégie touche le rang r sont un sous-ensemble QUELCONQUE
# des tirages où le rang r a payé. Le rapport moyen encaissé doit donc valoir
# le rapport moyen du rang. On teste exactement cela, rang par rang.
#
# Deux garde-fous, sans lesquels un écart ne vaudrait rien :
#   · la stratégie « hasard » doit sortir à zéro ;
#   · le rang placebo (m ≈ 0) doit sortir à zéro pour TOUTES les stratégies.

def grille_extreme(cfg, calib, sens: int = -1):
    """Grille gloutonne qui minimise (sens = −1) ou maximise (sens = +1) la
    log-popularité, co-occurrences comprises.

    Déterministe, donc exempte du bruit de générateur que `generer_grilles`
    introduit. C'est ce qui permet au backtest apparié de mesurer l'effet du
    partage plutôt que celui de la graine.
    """
    choisis: list[int] = []
    restants = list(nums(cfg))
    while len(choisis) < cfg["pick"] and restants:
        meilleur, score = None, None
        for n in restants:
            p = popularite_log(cfg, tuple(sorted([*choisis, n])), calib)
            if score is None or sens * p > sens * score:
                meilleur, score = n, p
        choisis.append(meilleur)
        restants.remove(meilleur)
    return tuple(sorted(choisis))


def bonus_extreme(cfg, calib, sens: int = -1):
    """Le(s) numéro(s) bonus le(s) moins joué(s) (sens = −1) ou le plus."""
    if calib:
        cle = sorted(bonus_nums(cfg),
                     key=lambda n: sens * calib["delta"].get(n, 0.0),
                     reverse=True)
    else:
        cle = sorted(bonus_nums(cfg),
                     key=lambda n: sens * _pop_bonus_heuristique(cfg, n),
                     reverse=True)
    return sorted(cle[:cfg["bonus_pick"]])


def backtest_partage(cfg, tirages, n_derniers=None, pas_calib: int = 25,
                     n_repetitions: int = 12, seed: int = 0):
    """Backtest apparié du partage. Voir le commentaire de section.

    Walk-forward strict : la calibration employée au tirage i n'est calculée
    que sur les tirages ANTÉRIEURS, et rafraîchie tous les `pas_calib` tirages
    comme en production.

    `n_repetitions` : nombre de grilles indépendantes tirées par tirage pour
    les stratégies à bonus aléatoire. Elles n'ont pas de grille canonique ;
    une seule par tirage rendrait leur estimation trop bruyante pour servir de
    témoin. L'inférence, elle, reste comptée en TIRAGES : deux grilles qui
    touchent le même rang le même soir encaissent le même rapport, elles
    n'apportent pas deux informations.

    DEUX LEVIERS, PAS UN — et il a fallu un test raté pour s'en apercevoir.
    Le n° Chance et les Étoiles ont leur propre popularité, et elle pèse lourd
    sur les rangs qui les exigent : à l'EuroMillions, jouer les deux étoiles
    les plus cochées fait chuter le rapport du rang « 1 + 2 étoiles » de 40 %,
    alors même que ce rang ne demande qu'UN bon numéro. Mélanger les deux
    leviers rendrait le rang placebo inutilisable — il bougerait pour une
    raison qui n'a rien à voir avec les boules.

    On les sépare donc : `anti_boules` ne joue que les boules délaissées, avec
    un bonus tiré au sort. L'écart entre `anti` et `anti_boules` mesure ce que
    le bonus apporte à lui seul, et les stratégies à bonus aléatoire rendent
    le placebo interprétable.
    """
    debut = max(MIN_TIRAGES_CALIBRATION, len(tirages) - (n_derniers or 10**9))
    if debut >= len(tirages):
        return None
    rng = random.Random(seed)
    univers = list(nums(cfg))
    anniv = [n for n in univers if n <= 31]

    # `bonus_neutre` : le bonus est tiré au sort, donc la stratégie ne joue que
    # sur les boules. Ce sont les seules comparables sur le rang placebo.
    noms = {"anti": False, "anti_boules": True, "hasard": True,
            "anniversaire": True, "populaire": False}
    # rapports encaissés : stratégie → rang → index du tirage → liste de gains.
    # Regrouper PAR TIRAGE dès l'accumulation est ce qui permet ensuite de
    # compter une observation par soir, et non une par grille.
    encaisse = {s: defaultdict(lambda: defaultdict(list)) for s in noms}
    mise = dict.fromkeys(noms, 0.0)
    gain = dict.fromkeys(noms, 0.0)
    n_grilles = dict.fromkeys(noms, 0)

    calib, prochaine, g_anti, g_pop, b_anti, b_pop = None, debut, None, None, None, None
    for i in range(debut, len(tirages)):
        if i >= prochaine:
            calib = calibration_empirique(cfg, tirages[:i])
            g_anti = grille_extreme(cfg, calib, -1)
            g_pop = grille_extreme(cfg, calib, +1)
            b_anti = bonus_extreme(cfg, calib, -1)
            b_pop = bonus_extreme(cfg, calib, +1)
            prochaine = i + pas_calib
        t = tirages[i]
        prix = prix_du_tirage(cfg, t["date"])
        bonus_alea = [rng.sample(list(bonus_nums(cfg)), cfg["bonus_pick"])
                      for _ in range(n_repetitions)]
        tirees = {
            "anti": [(list(g_anti), list(b_anti))],
            "populaire": [(list(g_pop), list(b_pop))],
            "anti_boules": [(list(g_anti), b) for b in bonus_alea],
            "hasard": [(rng.sample(univers, cfg["pick"]), b)
                       for b in bonus_alea],
            "anniversaire": [(rng.sample(anniv, cfg["pick"]), b)
                             for b in bonus_alea],
        }
        for s, grilles in tirees.items():
            for numeros, bons in grilles:
                r = regler_grille(cfg, {"numeros": numeros, "bonus": bons}, t)
                mise[s] += prix
                gain[s] += r["gain"]
                n_grilles[s] += 1
                if r["rang"] and r["gain"] > 0:
                    encaisse[s][r["rang"]][i].append(r["gain"])

    # Référence : ce que le rang r paie en moyenne, sur son SEUL régime
    # pari-mutuel. Avant le 04/11/2019 les rangs du Loto payaient un montant
    # fixe ; les mêler à la référence la déplacerait sans qu'aucune stratégie
    # n'y soit pour rien.
    testes = tirages[debut:]
    ref_moy, ref_log, ref_sd, ref_n = {}, {}, {}, {}
    for r in sorted(rangs_mb(cfg)):
        d = debut_pari_mutuel(testes, r)
        vals = [t["rapports"][r] for t in testes[d:]
                if t["rapports"].get(r) and t["rapports"][r] > 0]
        if len(vals) >= 30:
            logs = [math.log(v) for v in vals]
            ref_moy[r] = statistics.mean(vals)
            ref_log[r] = statistics.mean(logs)
            ref_sd[r] = statistics.pstdev(logs)
            ref_n[r] = len(vals)

    ra = rang_affluence(cfg, testes)
    strategies = {}
    for s in noms:
        rangs = {}
        retenus = set()
        for r in sorted(ref_moy):
            par_tirage = encaisse[s].get(r) or {}
            n_t = len(par_tirage)                 # tirages DISTINCTS touchés
            if n_t < MIN_TIRAGES_RANG_PARTAGE:
                continue
            retenus.add(r)
            # UNE observation par tirage : deux grilles qui touchent le même
            # rang le même soir encaissent le même rapport, elles n'apportent
            # pas deux informations. Compter les grilles biaiserait la moyenne
            # des stratégies dont le nombre de grilles gagnantes dépend du
            # tirage — « anniversaire » au premier chef.
            moyennes = [statistics.mean(v) for v in par_tirage.values()]
            moy_log = statistics.mean(math.log(v) for v in moyennes)
            ecart = moy_log - ref_log[r]
            # correction de population finie : le sous-échantillon touché est
            # inclus dans la référence, l'écart est donc moins dispersé que
            # ref_sd/√n ne le dit.
            fpc = math.sqrt(max(0.0, 1.0 - n_t / ref_n[r]))
            se = ref_sd[r] / math.sqrt(n_t) * fpc if ref_sd[r] > 1e-12 else 0.0
            rangs[r] = {
                "n_tirages": n_t,
                "rapport_moyen": round(statistics.mean(moyennes), 2),
                "rapport_reference": round(ref_moy[r], 2),
                "ecart_pct": round(100 * (math.exp(ecart) - 1), 2),
                "t": round(ecart / se, 2) if se > 1e-12 else 0.0,
                "placebo": r == ra,
            }
        # Surcote globale, en euros : à rangs touchés identiques, combien la
        # stratégie a-t-elle encaissé de plus que le rapport moyen de ces rangs.
        # Restreinte aux rangs PUBLIÉS : sans ce filtre, un rang touché deux
        # fois et absent du tableau pouvait porter 30 % du chiffre de tête.
        attendu = recu = 0.0
        for r in retenus:
            for vals in encaisse[s][r].values():
                m = statistics.mean(vals)
                attendu += ref_moy[r]
                recu += m
        strategies[s] = {
            "rangs": rangs,
            "bonus_neutre": noms[s],
            "n_grilles": n_grilles[s],
            "mise": round(mise[s], 2),
            "gain": round(gain[s], 2),
            "roi_pct": (round(100 * (gain[s] - mise[s]) / mise[s], 1)
                        if mise[s] else 0.0),
            "surcote_pct": (round(100 * (recu / attendu - 1), 2)
                            if attendu > 0 else None),
        }

    # Ce que le seul choix du bonus apporte : mêmes boules, bonus délaissé
    # contre bonus tiré au sort.
    levier_bonus = None
    if (strategies.get("anti", {}).get("surcote_pct") is not None
            and strategies.get("anti_boules", {}).get("surcote_pct")
            is not None):
        levier_bonus = round(strategies["anti"]["surcote_pct"]
                             - strategies["anti_boules"]["surcote_pct"], 2)

    return {
        "n_tirages": len(testes),
        "depart": tirages[debut]["date"].isoformat(),
        "rang_placebo": ra,
        "n_repetitions": n_repetitions,
        "strategies": strategies,
        "levier_bonus_pct": levier_bonus,
        "note": ("rapport encaissé sachant le rang touché ; la probabilité de "
                 "toucher ne dépend d'aucune stratégie"),
    }


def valeur_modes(cfg, tirages, ev_p, elast, calib, modes_grilles):
    """Ce que vaut RÉELLEMENT chaque mode, en % de la mise, face à une grille
    quelconque — gains courants seulement, jackpot exclu.

    Le mode « pronostic » y sort négatif, et c'est le point : il joue les
    numéros que tout le monde joue, donc il partage davantage. Un produit qui
    propose un mode sans dire ce qu'il coûte n'est pas honnête.
    """
    if not ev_p or not elast or not ev_p.get("ev_fixe"):
        return None
    fixe = ev_p["ev_fixe"]
    out = {}
    for mode, grilles in modes_grilles.items():
        if not grilles:
            continue
        vals, valsx = [], []
        for g in grilles:
            comp = g.get("pop_comp") or composantes_popularite(
                cfg, tuple(g["numeros"]), calib)
            vals.append(fixe * (facteur_partage(elast, comp) - 1.0))
            valsx.append(fixe * (facteur_partage(elast, comp, prudent=False)
                                 - 1.0))
        gagne = statistics.mean(vals)
        out[mode] = {
            "gain_euro": round(gagne, 4),
            "gain_pct_mise": round(100 * gagne / cfg["prix"], 2),
            "gain_pct_mise_extrapole": round(
                100 * statistics.mean(valsx) / cfg["prix"], 2),
            "pop_rel_moyen": round(
                statistics.mean(g["pop_rel"] for g in grilles), 3),
        }
    return out


# ==============================================================================
# 9. VÉRITÉ : BACKTEST · ANNIVERSAIRE · χ²
# ==============================================================================

def backtest(cfg, tirages, rng, depart: int = 60, fen_mom: int = 20):
    """Walk-forward : à chaque tirage, top-pick du folklore (fréq+retard+
    EWMA+momentum, incrémental) vs hasard. Attendu : pick²/n_max."""
    N = list(nums(cfg))
    freq, dernier = Counter(), dict.fromkeys(N, -1)
    ewma = dict.fromkeys(N, 0.0)
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


def numeros_les_plus_sortis(cfg, tirages, n_sim: int = 600, top: int = 6,
                            derniers: int = 500):
    """Le tableau que tout le monde veut voir — avec sa référence honnête.

    « Quels numéros sortent le plus ? » est LA question qu'on pose à un site de
    loto, et aucun n'y répond correctement. Le classement seul ne veut rien
    dire : sur 49 numéros, il y en a forcément un en tête. La seule lecture qui
    a du sens est de comparer le record observé à ce qu'une machine SANS
    DÉFAUT produit sur le même nombre de tirages.

    On rend donc trois choses ensemble, et jamais l'une sans les autres :
      · le classement (numéros et paires) ;
      · la plage qu'une machine parfaite produit, par simulation ;
      · ce que rapporte VRAIMENT le fait de jouer ces numéros, en euros,
        rejoué tirage par tirage sur le seul passé, contre un témoin au hasard.

    C'est la troisième qui répond à la question. Les deux premières servent à
    ne pas se tromper sur la deuxième.
    """
    n_max, pick = cfg["n_max"], cfg["pick"]
    n_t = len(tirages)
    if n_t < 200:
        return None

    freq = Counter()
    paires = Counter()
    for t in tirages:
        b = sorted(t["balls"])
        freq.update(b)
        for i in range(len(b)):
            for j in range(i + 1, len(b)):
                paires[(b[i], b[j])] += 1

    # Référence : ce qu'une machine parfaite produit. Graine fixe pour que
    # l'export reste reproductible d'une exécution à l'autre.
    rng = random.Random(0x10770)
    univers = list(nums(cfg))
    rec_num, rec_paire = [], []
    for k in range(n_sim):
        c, cp = Counter(), Counter()
        for _ in range(n_t):
            b = sorted(rng.sample(univers, pick))
            c.update(b)
            if k < n_sim // 4:
                for i in range(len(b)):
                    for j in range(i + 1, len(b)):
                        cp[(b[i], b[j])] += 1
        rec_num.append(max(c.values()))
        if cp:
            rec_paire.append(max(cp.values()))

    # Et si on les jouait ? Walk-forward strict, rapports FDJ réels.
    debut = max(200, n_t - derniers)

    def rejouer(choisir, graine=0):
        r = random.Random(graine)
        mise = gain = 0.0
        for i in range(debut, n_t):
            grille = choisir(tirages[:i], r)
            t = tirages[i]
            rang = rang_gagne(cfg, len(set(grille) & set(t["balls"])), 0)
            mise += prix_du_tirage(cfg, t["date"])
            if rang is not None:
                gain += t["rapports"].get(rang, 0.0) or 0.0
        return round(mise, 2), round(gain, 2)

    def chauds(passe, _r):
        c = Counter()
        for t in passe:
            c.update(t["balls"])
        return [n for n, _ in c.most_common(pick)]

    def hasard(_passe, r):
        return r.sample(univers, pick)

    mise, gain_chauds = rejouer(chauds)
    gains_hasard = [rejouer(hasard, graine=g)[1] for g in range(12)]

    ordre = freq.most_common()
    return {
        "n_tirages": n_t,
        "attendu_par_numero": round(n_t * pick / n_max, 1),
        "plus_sortis": [{"n": n, "sorties": v} for n, v in ordre[:top]],
        "moins_sortis": [{"n": n, "sorties": v} for n, v in ordre[-top:][::-1]],
        "paires_frequentes": [{"a": a, "b": b, "sorties": v}
                              for (a, b), v in paires.most_common(top)],
        "attendu_par_paire": round(
            n_t * (pick * (pick - 1) // 2) / (n_max * (n_max - 1) // 2), 1),
        # la référence honnête
        "record_hasard_min": min(rec_num), "record_hasard_max": max(rec_num),
        "record_paire_hasard_min": min(rec_paire) if rec_paire else None,
        "record_paire_hasard_max": max(rec_paire) if rec_paire else None,
        # l'épreuve des faits
        "epreuve": {
            "n_tirages": n_t - debut,
            "mise": mise,
            "gain_numeros_chauds": gain_chauds,
            "gain_hasard_median": round(statistics.median(gains_hasard), 2),
            "gain_hasard_min": round(min(gains_hasard), 2),
            "gain_hasard_max": round(max(gains_hasard), 2),
        },
    }


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
        num = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True))
        den = math.sqrt(sum((a - mx) ** 2 for a in xs)
                        * sum((b - my) ** 2 for b in ys)) or 1.0
        r = num / den
    return {"n": len(xs), "r": round(r, 3)}


def test_chi2(cfg, tirages):
    """χ² d'équiprobabilité des boules — le seul edge théorique (usure).

    Attention à la loi de référence : un tirage prend `pick` boules SANS
    REMISE, ce n'est donc PAS une multinomiale. Chaque boule sort avec
    p = pick/n_max, indépendamment d'un tirage à l'autre, d'où
        Var(O_i) = n·p(1−p)   et   E[(O_i−E)²/E] = 1−p,
    soit E[χ²] = n_max·(1−p) = n_max − pick — et non n_max − 1.

    La statistique suit de près (1−p)·χ²(n_max). Comparer sa valeur brute
    aux seuils d'un χ²(n_max−1) rend le test trop conservateur : mesuré par
    simulation, l'ancien « seuil 5 % » ne déclenchait qu'à 2-3 %. Un test
    qui alerte deux fois moins souvent qu'annoncé transforme son manque de
    sensibilité en fausse certitude (« boules équilibrées »).

    Seuils = (1 − pick/n_max) × quantiles d'un χ²(n_max), vérifiés par
    simulation dans tests/test_chi2.py.
    """
    QUANTILES = {49: (66.339, 74.919), 50: (67.505, 76.154)}
    c = Counter()
    for t in tirages:
        c.update(t["balls"])
    n_total = len(tirages) * cfg["pick"]
    attendu = n_total / cfg["n_max"]
    chi2 = sum((c.get(n, 0) - attendu) ** 2 / attendu for n in nums(cfg))
    ddl = cfg["n_max"]                     # ddl effectif de la loi mise à l'échelle
    echelle = 1.0 - cfg["pick"] / cfg["n_max"]
    q5, q1 = QUANTILES.get(ddl, (ddl + 2 * math.sqrt(2 * ddl),
                                 ddl + 3.1 * math.sqrt(2 * ddl)))
    s5, s1 = echelle * q5, echelle * q1
    return {"chi2": round(chi2, 2), "ddl": ddl,
            "esperance": cfg["n_max"] - cfg["pick"],
            "seuil_5pct": round(s5, 2), "seuil_1pct": round(s1, 2),
            "biais_detecte": chi2 > s5,
            "top_suspects": [n for n, _ in c.most_common(3)]}


# ==============================================================================
# 10. AFFICHAGE CONSOLE
# ==============================================================================

def barre(score, largeur=20):
    plein = round(score / 100 * largeur)
    return BAR_FULL * plein + BAR_EMPTY * (largeur - plein)


def prochain_tirage(cfg, aujourdhui: date, dernier_connu: date | None = None) -> date:
    """Premier jour de tirage à venir.

    `dernier_connu` évite un faux « prochain tirage : aujourd'hui » les soirs
    où le tirage a déjà eu lieu et figure déjà dans l'historique FDJ (cas
    courant pour l'EuroMillions du mardi, publié dans la nuit) : le prochain
    tirage est alors STRICTEMENT postérieur au dernier tirage connu.
    """
    d = aujourdhui
    if dernier_connu and d <= dernier_connu:
        d = dernier_connu + timedelta(days=1)
    for _ in range(9):
        if d.weekday() in cfg["jours"]:
            return d
        d += timedelta(days=1)
    return d


def afficher(cfg, ctx):
    p = print
    t0, t1 = ctx["tirages"][0]["date"], ctx["tirages"][-1]["date"]
    p("\n" + "═" * 68)
    p(f"   🎱  ORACLE v{VERSION} MAX — {cfg['nom']}")
    p("═" * 68)
    p(f"   Tirages analysés : {len(ctx['tirages'])}  "
      f"({t0:%d/%m/%Y} → {t1:%d/%m/%Y})")
    p(f"   Prochain tirage : {JOURS_FR[ctx['date_tirage'].weekday()]} "
      f"{ctx['date_tirage']:%d/%m/%Y}   |   Mode : {ctx['mode'].upper()}")
    if ctx.get("sources"):
        p(f"   Archives FDJ : {', '.join(lb for lb, _ in ctx['sources'])}")
    if ctx["jackpot"]:
        p(f"   Jackpot pris en compte : {ctx['jackpot']/1e6:.0f} M€")
    for a in ctx.get("alertes", []):
        icone = {"critique": "✗", "attention": "⚠", "info": "✔"}[a["niveau"]]
        p(f"   {icone} {a['message']}")
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
        p(f"   Rangs exploités : {c['rangs']} sur {c['n_tirages']} tirages "
          f"({c['n_lignes']} observations)")
        p("   Co-occurrences  : " + "  ".join(
            f"{nom}={c['theta'][nom]:+.3f}" for nom, _ in PAIRES_POPULARITE))

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
            ev = ev_grille(ctx["ev_p"], ctx["jackpot"], g["pop_rel"],
                           ctx.get("elast"), g.get("pop_comp"))
            ligne += f"   EV {ev['ev']:+.2f} €"
        p(ligne)
    if ctx["ev_p"]:
        e = ctx["ev_p"]
        p(f"   (participation estimée ≈ {e['n_est']:,} grilles/tirage · "
          f"gains hors-jackpot ≈ {e['ev_fixe']} €/grille)".replace(",", " "))

    el = ctx.get("elast")
    if el:
        p("\n▶ LE PARTAGE JOUE À TOUS LES RANGS (v2.8)")
        p("   Tous les rangs sont pari-mutuels : une part fixe de la cagnotte")
        p("   divisée par le nombre de gagnants. Jouer délaissé augmente donc")
        p("   le rapport à CHAQUE rang, pas seulement au jackpot.")
        p(f"   Élasticité mesurée sur {el['n_tirages']} tirages, rapports FDJ "
          "réels :")
        mb = rangs_mb(cfg)
        for r in sorted(el["beta"]):
            if el["n_obs"].get(r, 0) < MIN_OBS_RANG_ELASTICITE:
                continue
            t_ = el["t"].get(r)
            ligne_r = (f"     rang {r} (m={mb[r][0]:.2f})  "
                       f"β = {el['beta'][r]:+.4f}")
            if t_ is not None:
                ligne_r += f"   t = {t_:+7.1f}"
            if r == el["rang_affluence"]:
                ligne_r += "   ← PLACEBO (m≈0), doit valoir 0"
            p(ligne_r)
        if el["placebo"]:
            pl = el["placebo"]
            p(f"   Placebo ({pl['methode']}, {pl['n_essais']} essais) : "
              f"|t| max = {pl['t_max']} · {pl['n_significatifs']}"
              f"/{pl['n_coefficients']} coefficients significatifs")

    vm = ctx.get("valeur_modes")
    if vm:
        p("\n▶ CE QUE CHAQUE MODE VAUT, EN EUROS (gains courants, hors jackpot)")
        for mode in ("anti", "hybride", "pronostic"):
            v = vm.get(mode)
            if not v:
                continue
            signe = "gagne" if v["gain_euro"] >= 0 else "PERD"
            p(f"   · {mode:10s} partage ×{v['pop_rel_moyen']:.3f}  →  "
              f"{signe} {abs(v['gain_euro']):.4f} € par grille "
              f"({v['gain_pct_mise']:+.2f} % de la mise)")
        if vm.get("pronostic", {}).get("gain_pct_mise", 0) < 0:
            p("   Le mode « pronostic » joue les numéros que tout le monde")
            p("   joue : il partage plus, donc il DÉTRUIT de la valeur. Il est")
            p("   conservé pour le fun, jamais recommandé.")

    bp = ctx.get("partage")
    if bp:
        p(f"\n▶ BACKTEST APPARIÉ DU PARTAGE — {bp['n_tirages']} tirages "
          f"depuis {bp['depart']}")
        p("   Mesure : le rapport encaissé SACHANT qu'on a touché un rang.")
        p("   La probabilité de toucher, elle, ne dépend d'aucune stratégie.")
        for s in ("anti", "anti_boules", "hasard", "anniversaire",
                  "populaire"):
            d = bp["strategies"].get(s)
            if not d or d["surcote_pct"] is None:
                continue
            p(f"   · {s:13s} surcote du rapport {d['surcote_pct']:+6.2f} %"
              f"   ({d['n_grilles']} grilles réglées)")
        if bp.get("levier_bonus_pct") is not None:
            p(f"   Dont le seul choix du {cfg['bonus_nom']} : "
              f"{bp['levier_bonus_pct']:+.2f} % "
              "(« anti » et « anti_boules » jouent les mêmes boules)")
        p(f"   Rang placebo : {bp['rang_placebo']} — il doit rester à 0 pour")
        p("   les stratégies à bonus neutre, sans quoi la mesure capte autre")
        p("   chose que le partage des boules.")

    trj = ctx.get("trj")
    if trj:
        p("\n▶ CE QUE TU RÉCUPÈRES VRAIMENT (et non ce qui est annoncé)")
        p(f"   Sur {trj['n_tirages']} tirages, recomposé depuis les rapports "
          "FDJ réels :")
        p(f"   · retour total du jeu ........... {trj['trj_total']:.1%}  "
          "(≈ le « 50 % reversé aux joueurs » annoncé)")
        p(f"   · dont happé par le rang 1 ...... {trj['part_jackpot']:.1%} "
          "de la cagnotte")
        p(f"   · retour HORS jackpot ........... {trj['trj_hors_jackpot']:.1%} "
          f"→ {cfg['prix'] * trj['trj_hors_jackpot']:.2f} € rendus "
          f"par tranche de {cfg['prix']:.2f} €")
        p("   Le « 50 % » est vrai pour le JEU, pas pour TOI : la moitié de "
          "cette")
        p("   somme dort dans un jackpot qu'un joueur d'un ticket ne touchera")
        p("   jamais. Ton retour réel, c'est la troisième ligne.")

    if ctx["systeme"]:
        s = ctx["systeme"]
        p(f"\n▶ SYSTÈME RÉDUCTEUR — pool {s['pool']}")
        p("   Garantie VÉRIFIÉE : ≥3 bons numéros si les 5 sortants ∈ pool")
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

    rc = ctx.get("recherche")
    if rc:
        r, nul = rc["reel"], rc["nul"]
        p("\n▶ RECHERCHE DE FORMULE — le méga-algo, et son témoin")
        p(f"   {rc['budget_par_recherche']} formules essayées sur "
          f"{rc['n_tirages_evalues']} tirages. Hasard = {rc['theorique']:.4f} "
          "bon numéro/tirage.")
        p(f"   Vraies données : entraînement {r['score_entrainement']:.4f} → "
          f"VALIDATION {r['score_validation']:.4f}")
        p(f"   Témoin permuté : entraînement {nul['entrainement_moyen']:.4f} → "
          f"VALIDATION {nul['score_validation_moyen']:.4f} "
          f"(σ {nul['ecart_type']:.4f})")
        p(f"   Écart réel vs témoin : z = {rc['z_vs_nul']:+.2f} "
          f"· p empirique = {rc['p_empirique']:.3f} "
          f"({nul['au_moins_aussi_bien']}/{nul['n_essais']} témoins aussi bons)")
        p(f"   {rc['verdict']}")

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
        d = s.get("dispersion_anti")
        if d:
            p(f"   → Dispersion du mode anti sur {d['n_graines']} autres "
              f"graines, stratégie IDENTIQUE :")
            p(f"     {d['min']:.2f} € … {d['p10']:.2f} € … "
              f"médiane {d['mediane']:.2f} € … {d['p90']:.2f} € … "
              f"{d['max']:.2f} €")
            p("     Le chiffre ci-dessus est UNE réalisation, pas une moyenne.")
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

def _export_elasticite(cfg, el):
    """Sérialise l'élasticité : clés de rang en chaînes (contrainte JSON) et
    arrondis lisibles. Le `m` de chaque rang accompagne son beta — sans lui,
    la prédiction « |beta| croît avec m » n'est pas vérifiable par le lecteur.
    """
    if not el:
        return None
    mb = rangs_mb(cfg)
    rangs = {}
    for r in sorted(el["beta"]):
        if el["n_obs"].get(r, 0) < MIN_OBS_RANG_ELASTICITE:
            continue
        ch = el["charges"].get(r, (1.0, 1.0, 0.0))
        rangs[str(r)] = {
            "m": round(mb[r][0], 4),
            "b": round(mb[r][1], 4),
            "beta": round(el["beta"][r], 4),
            "charge_marginale": round(ch[0], 4),
            "charge_paires": round(ch[1], 4),
            "charge_paires_croisees": round(ch[2], 4),
            "se": (None if el["se"].get(r) is None
                   else round(el["se"][r], 4)),
            "t": (None if el["t"].get(r) is None else round(el["t"][r], 2)),
            "n_obs": el["n_obs"][r],
            "depuis_tirage": el["depuis"].get(r),
            "part_ev": round(el["parts"].get(r, 0.0), 4),
            "prix_fixe": el["n_obs"].get(r, 0) < MIN_OBS_RANG_ELASTICITE,
            # le rang d'affluence est le témoin : il voit la même foule que
            # les autres mais ne dépend presque pas des numéros tirés — la
            # page et le contrat l'affichent comme tel
            "placebo": r == el["rang_affluence"],
        }
    # Les rangs à prix fixe n'ont pas d'élasticité à estimer, mais ils pèsent
    # dans l'EV : les taire donnerait un total de parts inférieur à 1 sans
    # explication. Au Loto, le rang 9 vaut à lui seul 26 % des gains.
    fixes = {str(r): round(el["parts"].get(r, 0.0), 4)
             for r in sorted(el["parts"])
             if str(r) not in rangs and el["parts"].get(r, 0.0) > 0.001}
    return {
        "n_tirages": el["n_tirages"],
        "rang_affluence": el["rang_affluence"],
        # Les trois références, sans lesquelles `pop_comp` ne sert à rien : le
        # facteur de partage se calcule sur des ÉCARTS à la grille quelconque.
        # Les exporter, c'est permettre à la page — et au contrat — de
        # reproduire exactement le calcul du moteur au lieu de le croire.
        "references": [round(v, 4) for v in el["refs"]],
        "domaine_observe": {"plancher": round(el["plancher"], 4),
                            "plafond": round(el["plafond"], 4)},
        "placebo": el["placebo"],
        "rangs": rangs,
        "rangs_a_prix_fixe": fixes,
        "note": ("beta = élasticité du rapport à la log-popularité de la "
                 "combinaison sortie, tirée au sort par la FDJ ; estimée sur "
                 "le seul régime pari-mutuel de chaque rang"),
    }


def _export_partage(bp):
    """Sérialise le backtest apparié (clés de rang en chaînes)."""
    if not bp:
        return None
    out = dict(bp)
    out["strategies"] = {
        s: dict(d, rangs={str(r): v for r, v in d["rangs"].items()})
        for s, d in bp["strategies"].items()}
    return out


def export_web(chemin, cfg, ctx, args):
    dern = ctx["tirages"][-1]
    modes = {}
    for mode in ("hybride", "pronostic", "anti"):
        rng_m = random.Random(args.seed if args.seed is not None else 0)
        fin = score_final(cfg, ctx["folklore"], ctx["anti"], mode)
        grs = generer_grilles(cfg, fin, ctx["sb"], ctx["tirages"], mode,
                              max(args.grilles, 3), rng_m, ctx["calib"])
        # `vs_dernier` : ce que chaque grille affichée aurait fait au dernier
        # tirage réel, réglé aux rapports FDJ de ce soir-là. La page montre
        # ainsi chaque grille à côté des vraies boules, jamais dans le vide.
        grs = [dict(g, vs_dernier=regler_grille(cfg, g, dern)) for g in grs]
        modes[mode] = {
            "scores": {str(n): round(fin[n], 1) for n in nums(cfg)},
            "classement": sorted(nums(cfg), key=lambda n: -fin[n]),
            "bonus": {str(n): round(v, 1) for n, v in
                      score_bonus_mode(ctx["sb"], mode).items()},
            "grilles": grs,
        }
    systeme = ctx["systeme"]
    if systeme:
        systeme = dict(systeme, grilles=[
            dict(g, vs_dernier=regler_grille(cfg, g, dern))
            for g in systeme["grilles"]])
    calib = ctx["calib"]
    rapport = {
        "meta": {
            "version": VERSION, "source": "fdj",
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
            # v2.2 — traçabilité et transparence des données d'entrée
            "alertes": ctx.get("alertes", []),
            "archives": [lbl for lbl, _ in ctx.get("sources", [])],
            "epoques_exclues": cfg.get("exclus", {}),
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
            # `beta` : nom conservé pour la page. Contenu v2.4 = gamma, la
            # log-popularité À L'ÉCHELLE DU RANG 1. Les beta v2.3, estimés sur
            # le total des gagnants, en valaient ~0,17 fois.
            "beta": {str(n): round(calib["gamma"][n], 4) for n in nums(cfg)},
            "delta": {str(n): round(calib["delta"][n], 4)
                      for n in bonus_nums(cfg)},
            "theta": {nom: round(calib["theta"][nom], 5)
                      for nom, _ in PAIRES_POPULARITE},
            "t_median": calib["t_median"],
            "n_significatifs": calib["n_significatifs"],
            "rangs_utilises": calib["rangs"],
        }),
        "systeme": systeme,
        "historique": ctx.get("histo"),
        "simulation": ctx.get("sim"),
        # v2.8 — le partage joue à TOUS les rangs, et on le publie
        "elasticite": _export_elasticite(cfg, ctx.get("elast")),
        "verdicts": {"backtest": ctx["bt"],
                     "effet_anniversaire": ctx["pop"],
                     "frequences": ctx.get("freq_tab"),
                     "chi2": ctx["chi2"],
                     "trj": ctx.get("trj"),
                     "valeur_modes": ctx.get("valeur_modes"),
                     "partage": _export_partage(ctx.get("partage")),
                     "recherche": ctx.get("recherche")},
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
        description=f"ORACLE v{VERSION} MAX — Loto + EuroMillions")
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
    ap.add_argument("--recherche", type=int, nargs="?", const=400,
                    default=None, metavar="BUDGET",
                    help="Recherche exhaustive d'une formule prédictive, "
                         "validée hors échantillon contre un témoin permuté")
    ap.add_argument("--partage", type=int, nargs="?", const=0, default=None,
                    metavar="N",
                    help="Backtest apparié du partage sur les N derniers "
                         "tirages (0 = tout l'historique exploitable)")
    args = ap.parse_args()

    cfg = JEUX[args.jeu]
    rng = random.Random(args.seed)
    ref = (date.fromisoformat(args.aujourdhui) if args.aujourdhui
           else date.today())
    tirages, sources, alertes = charger_tirages(cfg, args, ref)
    date_tirage = prochain_tirage(cfg, ref, tirages[-1]["date"])

    calib = calibration_empirique(cfg, tirages)
    couches, folklore, anti, anti_mode = calculer_scores(
        cfg, tirages, date_tirage.weekday(), calib)
    final = score_final(cfg, folklore, anti, args.mode)
    sb = bonus_scores(cfg, tirages, calib)
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
                         "pop_rel": round(pop_rel_grille(cfg, g, calib), 3),
                         "pop_comp": [round(v, 4) for v in
                                      composantes_popularite(cfg, g, calib)]}
                        for i, g in enumerate(gr)],
            "cout": round(len(gr) * cfg["prix"], 2),
        }

    bt = None if args.no_backtest else backtest(cfg, tirages, rng)
    pop = test_popularite(tirages)
    chi2 = test_chi2(cfg, tirages)
    elast = elasticite_rangs(cfg, tirages, calib)

    ctx = {"tirages": tirages, "date_tirage": date_tirage, "mode": args.mode,
           "couches": couches, "folklore": folklore, "anti": anti,
           "anti_mode": anti_mode, "calib": calib, "final": final, "sb": sb,
           "grilles": grilles, "jackpot": jackpot, "ev_p": ev_p,
           "systeme": systeme, "bt": bt, "pop": pop, "chi2": chi2,
           "elast": elast,
           "trj": decomposition_trj(cfg, tirages),
           "alertes": alertes, "sources": sources}

    # Ce que vaut chaque mode, mesuré sur les grilles réellement publiables.
    # Calculé ici parce qu'il faut les grilles des TROIS modes, alors que
    # `grilles` ne porte que le mode demandé en ligne de commande.
    ctx["valeur_modes"] = valeur_modes(
        cfg, tirages, ev_p, elast, calib,
        {m: generer_grilles(cfg, score_final(cfg, folklore, anti, m), sb,
                            tirages, m, max(args.grilles, 3),
                            random.Random(args.seed or 0), calib)
         for m in ("hybride", "pronostic", "anti")})

    if args.partage is not None:
        print("\n   ⚖️  Backtest apparié du partage… (walk-forward strict)")
        ctx["partage"] = backtest_partage(cfg, tirages,
                                          n_derniers=args.partage or None,
                                          seed=args.seed or 0)

    if args.recherche:
        import recherche as _rech
        # 12 témoins : compromis entre temps de cron et p-valeur exploitable
        # (le plancher atteignable est 1/13 ≈ 0,077).
        print(f"\n   🔬 Recherche de formule ({args.recherche} candidates "
              f"+ 12 témoins permutés)… plusieurs minutes.")
        ctx["recherche"] = _rech.etude_complete(
            cfg, tirages, random.Random(args.seed or 0),
            budget=args.recherche, n_nuls=12)

    ctx["freq_tab"] = numeros_les_plus_sortis(cfg, tirages)
    ctx["histo"] = maj_historique(cfg, ctx, args)
    # 12 graines suffisent pour un intervalle honnête : ce qu'on veut dire au
    # lecteur, c'est « ce nombre aurait pu être très différent », pas un
    # percentile au centième. Coût borné, les scores n'étant calculés qu'une
    # fois par tirage.
    ctx["sim"] = (retro_simulation(cfg, tirages, args.simulation,
                                   seed=args.seed or 0,
                                   n_graines_dispersion=12)
                  if args.simulation else None)

    afficher(cfg, ctx)
    if args.export_web:
        export_web(args.export_web, cfg, ctx, args)
    if args.json:
        export_web(args.json, cfg, ctx, args)


if __name__ == "__main__":
    main()
