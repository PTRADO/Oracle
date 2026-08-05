#!/usr/bin/env python3
"""Test de mutation — mesure ce que la suite de tests est CAPABLE d'attraper.

Principe : on injecte délibérément un bug dans le moteur, on lance la suite,
et on regarde si elle rougit. Si elle reste verte, cette zone n'est pas
protégée — et c'est une découverte au même titre qu'un bug.

Motivation historique : l'inversion des rangs 6 et 7 de l'EuroMillions n'a pas
seulement échappé à la suite, elle y était VERROUILLÉE par un test qui
l'affirmait « documentée », plus une tolérance de 5 % ajoutée pour
l'accommoder. 136 tests verts ne prouvent rien tant qu'on n'a pas mesuré leur
pouvoir de détection.

    python3 tools/mutation.py                # toutes les mutations
    python3 tools/mutation.py rang parser    # celles dont le nom contient…
    python3 tools/mutation.py --liste

Une mutation est ATTRAPÉE si au moins un test échoue. Le script restaure
toujours les fichiers, y compris sur Ctrl-C.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

# (nom, fichier, avant, après)
# `avant` doit apparaître EXACTEMENT une fois — sinon la mutation est déclarée
# invalide plutôt que d'être appliquée au petit bonheur.
MUTATIONS: list[tuple[str, str, str, str]] = [
    # ---- table des rangs : le convertisseur grille → euros ----------------
    ("rangs_loto_5_6_intervertis", "oracle.py",
     "(3, 1): 5,\n                 (3, 0): 6,",
     "(3, 1): 6,\n                 (3, 0): 5,"),
    ("rangs_loto_7_8_intervertis", "oracle.py",
     "(2, 1): 7, (2, 0): 8}",
     "(2, 1): 8, (2, 0): 7}"),
    ("rangs_loto_3_4_intervertis", "oracle.py",
     "(4, 1): 3, (4, 0): 4,",
     "(4, 1): 4, (4, 0): 3,"),
    ("rangs_euro_6_7_intervertis", "oracle.py",
     "(3, 2): 6, (4, 0): 7,",
     "(3, 2): 7, (4, 0): 6,"),
    ("rangs_euro_12_13_intervertis", "oracle.py",
     "(2, 1): 12, (2, 0): 13}",
     "(2, 1): 13, (2, 0): 12}"),
    ("rang_chance_seul_exige_2_bons", "oracle.py",
     "if b == 1 and m <= 1:",
     "if b == 1 and m <= 2:"),

    # ---- probabilités et prix --------------------------------------------
    ("p_any_win_loto_arrondi_marketing", "oracle.py",
     '"p_any_win": 3_185_973 / 19_068_840,',
     '"p_any_win": 1 / 6,'),
    ("p_any_win_euro_decale_1pct", "oracle.py",
     '"p_any_win": 10_778_691 / 139_838_160,',
     '"p_any_win": 1.01 * 10_778_691 / 139_838_160,'),
    ("prix_historique_tarif_courant_partout", "oracle.py",
     '"prix_historique": [("2019-11-04", 2.20), ("1900-01-01", 2.00)],',
     '"prix_historique": [("1900-01-01", 2.20)],'),
    ("prix_bascule_decalee_d_un_jour", "oracle.py",
     '[("2019-11-04", 2.20), ("1900-01-01", 2.00)]',
     '[("2019-11-05", 2.20), ("1900-01-01", 2.00)]'),

    # ---- fuite du futur ---------------------------------------------------
    ("retro_simulation_fuite_du_futur", "oracle.py",
     'passe = tirages[:i]                       # strictement le passé',
     'passe = tirages[:i + 1]                   # MUTATION'),
    # Le backtest walk-forward absorbe le tirage APRÈS l'avoir prédit. Inverser
    # les deux lui fait voir le tirage sur lequel il parie : le « modèle » se
    # mettrait alors à battre le hasard, et le garde-fou n°1 du produit — « le
    # folklore ne prédit rien » — s'inverserait silencieusement.
    ("backtest_absorbe_avant_de_predire", "oracle.py",
     "        for n in N:\n"
     "            ewma[n] *= decay\n"
     "        for b in t[\"balls\"]:\n"
     "            freq[b] += 1\n"
     "            dernier[b] = i\n"
     "            ewma[b] += 1.0\n"
     "        fen.append(t[\"balls\"])\n"
     "        if len(fen) > fen_mom:\n"
     "            fen.popleft()\n"
     "    theo =",
     "    theo ="),

    # ---- anti-partage (v2.4) ---------------------------------------------
    ("anti_partage_signe_inverse", "oracle.py",
     'return (normaliser({n: -calib["gamma"][n] for n in nums(cfg)}),',
     'return (normaliser({n: calib["gamma"][n] for n in nums(cfg)}),'),
    ("anti_partage_bonus_signe_inverse", "oracle.py",
     'return normaliser({n: -calib["delta"][n] for n in bonus_nums(cfg)})',
     'return normaliser({n: calib["delta"][n] for n in bonus_nums(cfg)})'),
    ("popularite_ignore_les_co_occurrences", "oracle.py",
     '    s += somme_paires(balls, calib["table_paires"])',
     '    s += 0.0'),
    ("m_effectif_du_rang_chance_seul_mis_a_zero", "oracle.py",
     "            acc[r] = (sm + m * cas, sb + b * cas, sc + cas)",
     "            acc[r] = (sm + m * cas * (m > 1), sb + b * cas, sc + cas)"),
    ("calibration_sans_effet_fixe_de_tirage", "oracle.py",
     "            yy = y - ybar",
     "            yy = y"),
    ("rangs_denses_accepte_les_rangs_creux", "oracle.py",
     "def rangs_denses(cfg, tirages, w_min: int = 30, part: float = 0.99):",
     "def rangs_denses(cfg, tirages, w_min: int = 0, part: float = 0.0):"),

    # ---- contraintes de forme --------------------------------------------
    ("grille_valide_sans_contrainte_de_parite", "oracle.py",
     "    if pairs not in (2, 3):\n        return False",
     "    pass"),
    ("grille_valide_sans_contrainte_de_dizaines", "oracle.py",
     "    if len({(b - 1) // 10 for b in balls}) < 3:\n        return False",
     "    pass"),

    # ---- règlement --------------------------------------------------------
    ("reglement_rate_le_dernier_bon_numero", "oracle.py",
     "    bons = sorted(set(grille[\"numeros\"]) & set(tirage[\"balls\"]))",
     "    bons = sorted(set(grille[\"numeros\"]) & set(tirage[\"balls\"][:-1]))"),

    # ---- les correctifs de la chasse d'août 2026 --------------------------
    # Si l'une de ces trois survit, le test écrit avec le correctif ne sait
    # pas rougir, et le correctif n'est pas protégé.
    ("parser_relit_les_gagnants_europeens", "oracle.py",
     'or "en_europe" in h)',
     ')'),
    ("normaliser_sans_bornage", "oracle.py",
     "    return {k: min(100.0, max(0.0, 100.0 * (v - lo) / ecart))\n"
     "            for k, v in scores.items()}",
     "    return {k: 100.0 * (v - lo) / ecart for k, v in scores.items()}"),
    ("pop_rel_sans_normalisation", "oracle.py",
     '    s -= calib["log_norm"] if bonus else calib["log_norm_nums"]',
     "    s -= 0.0"),

    # ---- recherche de formule --------------------------------------------
    ("recherche_sans_separation_entrainement_validation", "recherche.py",
     "    coupure = int(len(X) * part_entrainement)",
     "    coupure = len(X)"),
    ("recherche_temoin_non_permute", "recherche.py",
     "    rng.shuffle(boules)",
     "    pass"),
    ("recherche_fuite_du_futur", "recherche.py",
     "        if idx >= depart:\n"
     "            X.append(etat.traits(t[\"jour\"]))\n"
     "            cibles.append(set(t[\"balls\"]))\n"
     "        etat.absorber(t)",
     "        etat.absorber(t)\n"
     "        if idx >= depart:\n"
     "            X.append(etat.traits(t[\"jour\"]))\n"
     "            cibles.append(set(t[\"balls\"]))"),
    ("recherche_deux_traits_permutes", "recherche.py",
     '    "freq_50",        # T1 fenêtre courte\n'
     '    "freq_250",       # T1 fenêtre longue',
     '    "freq_250",       # MUTATION\n'
     '    "freq_50",        # MUTATION'),
]


def appliquer(nom: str, fichier: str, avant: str, apres: str) -> str | None:
    """Applique la mutation ; rend le texte original, ou None si impossible."""
    chemin = RACINE / fichier
    original = chemin.read_text(encoding="utf-8")
    if original.count(avant) != 1:
        return None
    chemin.write_text(original.replace(avant, apres, 1), encoding="utf-8")
    return original


def lancer_suite(rapide: bool) -> tuple[bool, str]:
    """(la suite rougit-elle ?, premier test en échec)."""
    cmd = [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header",
           "-p", "no:cacheprovider"]
    if rapide:
        cmd.append("-x")
    r = subprocess.run(cmd, cwd=RACINE, capture_output=True, text=True)
    sortie = r.stdout + r.stderr
    coupable = ""
    for ligne in sortie.splitlines():
        if ligne.startswith("FAILED") or ligne.startswith("ERROR"):
            coupable = ligne.split(" - ")[0].removeprefix("FAILED ").strip()
            break
    return r.returncode != 0, coupable


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("filtres", nargs="*", help="sous-chaînes du nom")
    ap.add_argument("--liste", action="store_true")
    ap.add_argument("--complet", action="store_true",
                    help="ne pas s'arrêter au premier test rouge (plus lent)")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    choisies = [m for m in MUTATIONS
                if not args.filtres or any(f in m[0] for f in args.filtres)]
    if args.liste:
        for nom, fic, _, _ in choisies:
            print(f"{fic:14s} {nom}")
        return 0

    print(f"→ {len(choisies)} mutations, suite complète à chaque fois\n")
    resultats = []
    for i, (nom, fichier, avant, apres) in enumerate(choisies, 1):
        t0 = time.time()
        original = appliquer(nom, fichier, avant, apres)
        if original is None:
            print(f"[{i:2d}/{len(choisies)}] {nom:52s} INVALIDE "
                  f"(motif absent ou multiple)")
            resultats.append({"mutation": nom, "fichier": fichier,
                              "statut": "invalide"})
            continue
        try:
            attrapee, coupable = lancer_suite(not args.complet)
        finally:
            (RACINE / fichier).write_text(original, encoding="utf-8")
        dt = time.time() - t0
        marque = "ATTRAPÉE" if attrapee else "**SURVIT**"
        print(f"[{i:2d}/{len(choisies)}] {nom:52s} {marque:10s} "
              f"{dt:5.1f}s  {coupable}")
        resultats.append({"mutation": nom, "fichier": fichier,
                          "statut": "attrapee" if attrapee else "survit",
                          "test": coupable, "secondes": round(dt, 1)})

    valides = [r for r in resultats if r["statut"] != "invalide"]
    pris = [r for r in valides if r["statut"] == "attrapee"]
    survivants = [r for r in valides if r["statut"] == "survit"]
    taux = len(pris) / len(valides) if valides else 0.0
    print(f"\n{'='*72}")
    print(f"TAUX DE DÉTECTION : {len(pris)}/{len(valides)} = {taux:.0%}")
    if survivants:
        print("\nSURVIVANTES — zones non protégées :")
        for r in survivants:
            print(f"  · {r['mutation']}  ({r['fichier']})")
    if args.json:
        args.json.write_text(json.dumps(
            {"taux": taux, "resultats": resultats}, indent=2,
            ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrompu — vérifie `git status` avant de continuer")
        sys.exit(130)
