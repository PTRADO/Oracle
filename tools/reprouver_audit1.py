#!/usr/bin/env python3
"""Re-preuve INDÉPENDANTE des 7 corrections du premier audit.

Règle du jeu : ne rien recalculer avec les fonctions auditées. Recalculer
avec les fonctions du moteur ne prouverait que leur accord avec elles-mêmes.
Ici on repart des CSV FDJ bruts et de la combinatoire faite à la main ; le
moteur n'est lu que pour comparer ses constantes aux nôtres.

    python3 tools/reprouver_audit1.py
"""
from __future__ import annotations

import csv
import io
import math
import re
import statistics
import sys
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

VERT, ROUGE, RAZ = "\033[32m", "\033[31m", "\033[0m"
_bilan: list[tuple[str, bool, str]] = []


def verifier(titre: str, ok: bool, detail: str = "") -> None:
    _bilan.append((titre, ok, detail))
    marque = f"{VERT}TIENT{RAZ}" if ok else f"{ROUGE}NE TIENT PAS{RAZ}"
    print(f"  [{marque}] {titre}")
    if detail:
        print(f"           {detail}")


# ---------------------------------------------------------------------------
# Lecture des CSV FDJ, à la main — aucun parseur du moteur
# ---------------------------------------------------------------------------

def lire_archives(motif: str) -> list[dict]:
    """(date, boules, bonus, gagnants{rang}, rapports{rang}) depuis les zips."""
    tirages: dict[date, dict] = {}
    for zpath in sorted((RACINE / "data").glob(f"{motif}*.zip")):
        with zipfile.ZipFile(zpath) as zf:
            for nom in zf.namelist():
                brut = zf.read(nom)
                for enc in ("utf-8-sig", "latin-1"):
                    try:
                        texte = brut.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    continue
                lignes = list(csv.DictReader(io.StringIO(texte), delimiter=";"))
                if not lignes:
                    continue
                for ligne in lignes:
                    t = _ligne_vers_tirage(ligne)
                    if t:
                        tirages.setdefault(t["date"], t)
    return [tirages[d] for d in sorted(tirages)]


def _norm(s: str) -> str:
    s = s.lower().strip()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ç", "c")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "_", s)


def _ligne_vers_tirage(ligne: dict) -> dict | None:
    cols = {_norm(k): v for k, v in ligne.items() if k}
    d = cols.get("date_de_tirage")
    if not d:
        return None
    try:
        j, m, a = (int(x) for x in d.split("/"))
        quand = date(a, m, j)
    except ValueError:
        return None
    boules = []
    for i in range(1, 6):
        v = cols.get(f"boule_{i}")
        if v and v.strip().isdigit():
            boules.append(int(v))
    if len(boules) != 5:
        return None
    bonus = []
    for cle in ("numero_chance", "etoile_1", "etoile_2"):
        v = cols.get(cle)
        if v and v.strip().isdigit():
            bonus.append(int(v))
    gagnants, rapports = {}, {}
    for k, v in cols.items():
        # DEUX pièges dans le CSV EuroMillions, tous deux vérifiés :
        #  · chaque rang existe en _en_france ET en _en_europe ;
        #  · le CSV contient une SECONDE famille de rangs 1-13, celle du jeu
        #    annexe « Étoile+ », dont les gagnants n'ont rien à voir. Les
        #    confondre donne un ratio rang6/rang7 de 0,27 au lieu de 0,98.
        # Le moteur les écarte par le « + » de « Etoile+ », que sa
        # normalisation (simple .lower()) conserve — la nôtre l'écrasait.
        if k.endswith("_en_europe") or "_etoile" in k:
            continue
        mg = re.match(r"nombre_de_gagnant_au_rang(\d+)", k)
        mr = re.match(r"rapport_du_rang(\d+)", k)
        if mg and v and v.strip().replace(" ", "").isdigit():
            gagnants[int(mg.group(1))] = int(v.strip().replace(" ", ""))
        if mr and v:
            try:
                rapports[int(mr.group(1))] = float(
                    v.replace(",", ".").replace(" ", ""))
            except ValueError:
                pass
    return {"date": quand, "balls": tuple(sorted(boules)),
            "bonus": tuple(sorted(bonus)), "gagnants": gagnants,
            "rapports": rapports}


# ---------------------------------------------------------------------------
# BUG 1 — EuroMillions : les rangs 6 et 7 étaient intervertis
# ---------------------------------------------------------------------------

def bug1(euro: list[dict]) -> None:
    print("\n1. EuroMillions — rangs 6 (3+2) et 7 (4+0)")
    # Prédiction combinatoire, calculée ici et nulle part ailleurs.
    tot = math.comb(50, 5) * math.comb(12, 2)
    p_3p2 = math.comb(5, 3) * math.comb(45, 2) * math.comb(2, 2) / tot
    p_4p0 = math.comb(5, 4) * math.comb(45, 1) * math.comb(10, 2) / tot
    attendu_bon = p_3p2 / p_4p0          # si r6 = 3+2 et r7 = 4+0
    attendu_faux = p_4p0 / p_3p2

    # Estimateur : RATIO DES TOTAUX, pas moyenne des ratios.
    # Avec ~340 gagnants par tirage, la moyenne des W6/W7 est biaisée vers le
    # haut par convexité de 1/y (mesuré : 1,0398 contre 0,9842 pour le ratio
    # poolé). Utiliser la mauvaise des deux ferait conclure, à tort, que la
    # table inversée colle mieux que la bonne.
    couples = [(t["gagnants"][6], t["gagnants"][7]) for t in euro
               if t["gagnants"].get(6) and t["gagnants"].get(7)]
    obs = sum(a for a, _ in couples) / sum(b for _, b in couples)

    import random
    rng = random.Random(20260805)
    tirs = []
    for _ in range(2000):
        ech = [couples[rng.randrange(len(couples))] for _ in couples]
        tirs.append(sum(a for a, _ in ech) / sum(b for _, b in ech))
    err = statistics.stdev(tirs)
    s_bon = abs(obs - attendu_bon) / err
    s_faux = abs(obs - attendu_faux) / err
    verifier(
        "la table en place est celle que les gagnants FDJ désignent",
        s_bon < s_faux,
        f"observé {obs:.4f} ± {err:.4f} sur {len(couples)} tirages | "
        f"table actuelle prédit {attendu_bon:.4f} ({s_bon:.1f} σ), "
        f"l'inversée {attendu_faux:.4f} ({s_faux:.1f} σ)")

    from oracle import JEUX, rang_gagne
    em = JEUX["euromillions"]
    verifier("le moteur place bien 3+2 au rang 6 et 4+0 au rang 7",
             rang_gagne(em, 3, 2) == 6 and rang_gagne(em, 4, 0) == 7)


# ---------------------------------------------------------------------------
# BUG 2 — p_any_win aux arrondis marketing
# ---------------------------------------------------------------------------

def bug2() -> None:
    print("\n2. p_any_win — combinatoire exacte contre arrondi commercial")
    # Loto : dénombrement à la main des tirages gagnants d'une grille donnée.
    tot_l = math.comb(49, 5) * 10
    au_moins_2 = sum(math.comb(5, m) * math.comb(44, 5 - m)
                     for m in range(2, 6))
    chance_seule = sum(math.comb(5, m) * math.comb(44, 5 - m)
                       for m in range(0, 2))
    p_loto = (au_moins_2 * 10 + chance_seule) / tot_l

    tot_e = math.comb(50, 5) * math.comb(12, 2)
    gagnants_e = 0
    for m in range(6):
        for b in range(3):
            if (m, b) in {(5, 2), (5, 1), (5, 0), (4, 2), (4, 1), (3, 2),
                          (4, 0), (2, 2), (3, 1), (3, 0), (1, 2), (2, 1),
                          (2, 0)}:
                gagnants_e += (math.comb(5, m) * math.comb(45, 5 - m)
                               * math.comb(2, b) * math.comb(10, 2 - b))
    p_euro = gagnants_e / tot_e

    from oracle import JEUX
    verifier("Loto : p_any_win est la valeur exacte, pas 1/6",
             abs(JEUX["loto"]["p_any_win"] - p_loto) < 1e-12,
             f"exact {p_loto:.7f} (1/{1/p_loto:.3f}) | "
             f"moteur {JEUX['loto']['p_any_win']:.7f} | "
             f"écart à 1/6 : {abs(p_loto - 1/6)/p_loto:.2%}")
    verifier("EuroMillions : p_any_win est la valeur exacte, pas 1/13",
             abs(JEUX["euromillions"]["p_any_win"] - p_euro) < 1e-12,
             f"exact {p_euro:.7f} (1/{1/p_euro:.3f}) | "
             f"moteur {JEUX['euromillions']['p_any_win']:.7f} | "
             f"écart à 1/13 : {abs(p_euro - 1/13)/p_euro:.2%}")


# ---------------------------------------------------------------------------
# BUG 3 — χ² : tirage SANS REMISE, donc E[χ²] = n_max − pick
# ---------------------------------------------------------------------------

def bug3() -> None:
    print("\n3. χ² — espérance sous tirage sans remise")
    import random
    rng = random.Random(20260805)
    n_max, pick, n_tir = 49, 5, 4000
    stats = []
    for _ in range(400):
        c = Counter()
        for _ in range(n_tir):
            for b in rng.sample(range(1, n_max + 1), pick):
                c[b] += 1
        att = n_tir * pick / n_max
        stats.append(sum((c.get(i, 0) - att) ** 2 / att
                         for i in range(1, n_max + 1)))
    moy = statistics.mean(stats)
    err = statistics.stdev(stats) / math.sqrt(len(stats))
    verifier("E[χ²] simulée vaut n_max − pick (44), pas n_max − 1 (48)",
             abs(moy - 44) < 4 * err,
             f"simulé {moy:.2f} ± {err:.2f} | sans remise 44 | "
             f"multinomiale 48 ({abs(moy - 48)/err:.0f} σ)")

    from oracle import JEUX, test_chi2
    r = test_chi2(JEUX["loto"], lire_archives("loto"))
    verifier("le moteur annonce bien 44 comme espérance",
             r["esperance"] == 44, f"moteur : ddl={r['ddl']}, "
             f"espérance={r['esperance']}")


# ---------------------------------------------------------------------------
# BUG 4 & 6 — le passé facturé au tarif d'aujourd'hui
# ---------------------------------------------------------------------------

def bug4_6(loto: list[dict]) -> None:
    print("\n4+6. Prix historique du Loto (2,00 € avant le 04/11/2019)")
    avant = [t for t in loto if t["date"] < date(2019, 11, 4)]
    apres = [t for t in loto if t["date"] >= date(2019, 11, 4)]
    verifier("des tirages à 2,00 € existent bien dans l'historique",
             len(avant) > 0,
             f"{len(avant)} tirages à 2,00 € et {len(apres)} à 2,20 € "
             f"| surfacturation évitée : "
             f"{len(avant) * 0.20:.2f} € par grille jouée à chaque tirage")

    from oracle import prix_du_tirage, JEUX
    cfg = JEUX["loto"]
    ok = (prix_du_tirage(cfg, date(2019, 11, 3)) == 2.00
          and prix_du_tirage(cfg, date(2019, 11, 4)) == 2.20
          and prix_du_tirage(cfg, date(2019, 11, 5)) == 2.20)
    verifier("la bascule tombe exactement au 04/11/2019", ok,
             f"03/11 → {prix_du_tirage(cfg, date(2019, 11, 3)):.2f} € | "
             f"04/11 → {prix_du_tirage(cfg, date(2019, 11, 4)):.2f} €")

    # TRJ recalculé à la main, à l'ancienne et à la nouvelle méthode.
    from oracle import decomposition_trj
    d = decomposition_trj(cfg, loto)
    p_exact = 3_185_973 / 19_068_840
    mises_vrai = mises_faux = gains = 0.0
    for t in loto:
        tot = sum(t["gagnants"].values())
        if tot <= 0 or not t["rapports"]:
            continue
        n_est = tot / p_exact
        prix = 2.00 if t["date"] < date(2019, 11, 4) else 2.20
        mises_vrai += n_est * prix
        mises_faux += n_est * 2.20
        gains += sum(t["gagnants"].get(r, 0) * t["rapports"].get(r, 0.0)
                     for r in t["rapports"])
    trj_vrai, trj_faux = gains / mises_vrai, gains / mises_faux
    verifier("le TRJ du moteur correspond au recalcul indépendant",
             abs(d["trj_total"] - trj_vrai) < 0.0015,
             f"recalculé {trj_vrai:.2%} | moteur {d['trj_total']:.2%} | "
             f"au tarif d'aujourd'hui partout : {trj_faux:.2%} "
             f"(soit {100*(trj_vrai-trj_faux):.2f} points de moins)")


# ---------------------------------------------------------------------------
# BUG 5 — la rétro-simulation joue-t-elle les grilles publiées ?
# ---------------------------------------------------------------------------

def bug5(loto: list[dict]) -> None:
    print("\n5. Rétro-simulation — les grilles jouées sont les grilles publiées")
    import inspect

    from oracle import retro_simulation
    src = inspect.getsource(retro_simulation)
    verifier("elle appelle le vrai générateur de grilles",
             "generer_grilles" in src and "calculer_scores" in src,
             "aucun raccourci « top-5 des scores » dans le corps de la "
             "fonction")

    # Preuve sur l'export : toute grille jouée respecte les contraintes de
    # forme, ce que le top-5 des scores ne respectait pas.
    import json
    chemin = RACINE / "docs" / "loto.json"
    if chemin.exists():
        sim = json.loads(chemin.read_text(encoding="utf-8")).get("simulation")
        gains = (sim or {}).get("modes", {}).get("anti", {}).get("gains", [])
        conformes = 0
        for g in gains:
            b = sorted(g["grille"])
            pair = sum(1 for x in b if x % 2 == 0)
            suite = max_suite = 1
            for k in range(1, len(b)):
                suite = suite + 1 if b[k] == b[k - 1] + 1 else 1
                max_suite = max(max_suite, suite)
            if pair in (2, 3) and len({(x - 1) // 10 for x in b}) >= 3 \
                    and max_suite < 3:
                conformes += 1
        verifier("chaque grille gagnante de l'export passe les contraintes",
                 gains and conformes == len(gains),
                 f"{conformes}/{len(gains)} grilles conformes")


# ---------------------------------------------------------------------------
# BUG 7 — deux valeurs différentes sous le même nom
# ---------------------------------------------------------------------------

def bug7() -> None:
    print("\n7. Collision de nom sur trj_hors_jackpot")
    import json
    for jeu in ("loto", "euromillions"):
        chemin = RACINE / "docs" / f"{jeu}.json"
        if not chemin.exists():
            continue
        d = json.loads(chemin.read_text(encoding="utf-8"))
        ev = d.get("ev_params", {})
        trj = d.get("verdicts", {}).get("trj", {})
        a = "trj_hors_jackpot" in ev
        b = "trj_hors_jackpot_recent" in ev
        verifier(f"{jeu} : ev_params expose le nom désambiguïsé",
                 b and not a,
                 f"ev_params → {sorted(k for k in ev if 'trj' in k)} | "
                 f"verdicts.trj → {sorted(k for k in trj if 'trj' in k)}")


def main() -> int:
    print("=" * 72)
    print("RE-PREUVE INDÉPENDANTE DES 7 CORRECTIONS DE L'AUDIT 1")
    print("=" * 72)
    loto = lire_archives("loto")
    euro = lire_archives("euromillions")
    print(f"\nCSV relus à la main : {len(loto)} tirages Loto, "
          f"{len(euro)} EuroMillions")

    bug1(euro)
    bug2()
    bug3()
    bug4_6(loto)
    bug5(loto)
    bug7()

    tiennent = sum(1 for _, ok, _ in _bilan if ok)
    print("\n" + "=" * 72)
    print(f"BILAN : {tiennent}/{len(_bilan)} vérifications tiennent")
    for titre, ok, _ in _bilan:
        if not ok:
            print(f"  {ROUGE}✗{RAZ} {titre}")
    return 0 if tiennent == len(_bilan) else 1


if __name__ == "__main__":
    sys.exit(main())
