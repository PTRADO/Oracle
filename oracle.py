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

VERSION = "2.4"          # bump obligatoire à tout changement du contrat JSON

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
                     iters: int = 30000):
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
    """
    debut = max(60, len(tirages) - n_derniers)
    calib, prochaine_calib = None, debut
    res = {m: {"mise": 0.0, "gain": 0.0, "rangs": Counter(),
               "n_gains": 0, "meilleur_gain": 0.0, "meilleur_date": None,
               "gains": []}
           for m in ("hybride", "pronostic", "anti")}

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
            r = regler_grille(cfg, {"numeros": grille, "bonus": bons_b}, t)
            res[mode]["mise"] += prix_du_tirage(cfg, t["date"])
            res[mode]["gain"] += r["gain"]
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

    n_sim = len(tirages) - debut
    return {"n_tirages": n_sim,
            "note": "grilles réellement générées, réglées aux rapports FDJ",
            "prix": cfg["prix"],
            "modes": {m: {"mise": round(v["mise"], 2),
                          "gain": round(v["gain"], 2),
                          "roi_pct": round(100 * (v["gain"] - v["mise"])
                                           / v["mise"], 1) if v["mise"] else 0,
                          "n_gains": v["n_gains"],
                          "gains": sorted(v["gains"], key=lambda g: -g["gain"]),
                          "meilleur_gain": round(v["meilleur_gain"], 2),
                          "meilleur_date": v["meilleur_date"],
                          "rangs": dict(sorted(v["rangs"].items()))}
                      for m, v in res.items()}}


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
        p(f"   Co-occurrences  : " + "  ".join(
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
            ev = ev_grille(ctx["ev_p"], ctx["jackpot"], g["pop_rel"])
            ligne += f"   EV {ev['ev']:+.2f} €"
        p(ligne)
    if ctx["ev_p"]:
        e = ctx["ev_p"]
        p(f"   (participation estimée ≈ {e['n_est']:,} grilles/tirage · "
          f"gains hors-jackpot ≈ {e['ev_fixe']} €/grille)".replace(",", " "))

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
        "systeme": ctx["systeme"],
        "historique": ctx.get("histo"),
        "simulation": ctx.get("sim"),
        "verdicts": {"backtest": ctx["bt"],
                     "effet_anniversaire": ctx["pop"],
                     "chi2": ctx["chi2"],
                     "trj": ctx.get("trj"),
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
           "systeme": systeme, "bt": bt, "pop": pop, "chi2": chi2,
           "trj": decomposition_trj(cfg, tirages),
           "alertes": alertes, "sources": sources}

    if args.recherche:
        import recherche as _rech
        # 12 témoins : compromis entre temps de cron et p-valeur exploitable
        # (le plancher atteignable est 1/13 ≈ 0,077).
        print(f"\n   🔬 Recherche de formule ({args.recherche} candidates "
              f"+ 12 témoins permutés)… plusieurs minutes.")
        ctx["recherche"] = _rech.etude_complete(
            cfg, tirages, random.Random(args.seed or 0),
            budget=args.recherche, n_nuls=12)

    ctx["histo"] = maj_historique(cfg, ctx, args)
    ctx["sim"] = (retro_simulation(cfg, tirages, args.simulation,
                                   seed=args.seed or 0)
                  if args.simulation else None)

    afficher(cfg, ctx)
    if args.export_web:
        export_web(args.export_web, cfg, ctx, args)
    if args.json:
        export_web(args.json, cfg, ctx, args)


if __name__ == "__main__":
    main()
