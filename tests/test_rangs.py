"""Validation de la table des rangs officiels (`rang_gagne`).

C'est elle qui convertit une grille en euros dans le grand livre. Une erreur
ici ne planterait rien : elle produirait un ROI faux — exactement le genre de
mensonge que ce produit refuse. On la vérifie donc de deux façons
indépendantes :

  1. combinatoire pure : la somme des probabilités de tous les rangs doit
     redonner le « 1 chance sur 6 » (Loto) / « sur 13 » (EuroMillions)
     annoncé par la FDJ et utilisé pour estimer la participation ;
  2. cohérence : chaque (bons, bonus) gagnant tombe dans un rang unique, et
     les rangs sont d'autant mieux payés qu'ils sont improbables.
"""
from __future__ import annotations

import math
import statistics
from pathlib import Path

import pytest

from oracle import JEUX, _texte_archive, parser_csv, rang_gagne, regler_grille

TOUS = [JEUX["loto"], JEUX["euromillions"]]
IDS = ["loto", "euromillions"]

DATA = Path(__file__).resolve().parent.parent / "data"


def tirages_des_archives(cle: str) -> list[dict]:
    """Tous les tirages réels des archives FDJ versionnées dans `data/`,
    dédoublonnés par date. Hors ligne : rien n'est téléchargé."""
    cfg = JEUX[cle]
    par_date: dict = {}
    for arc in cfg["archives"]:
        chemin = DATA / f"{arc['label']}.zip"
        if not chemin.exists():
            continue
        for t in parser_csv(cfg, _texte_archive(chemin.read_bytes()),
                            tolerant=True):
            par_date.setdefault(t["date"], t)
    return [par_date[d] for d in sorted(par_date)]


def proba_par_rang(cfg) -> dict[int, float]:
    n, k = cfg["n_max"], cfg["pick"]
    bmax, bp = cfg["bonus_max"], cfg["bonus_pick"]
    total = math.comb(n, k) * math.comb(bmax, bp)
    p: dict[int, float] = {}
    for m in range(k + 1):
        for b in range(bp + 1):
            rang = rang_gagne(cfg, m, b)
            if rang is None:
                continue
            cas = (math.comb(k, m) * math.comb(n - k, k - m)
                   * math.comb(bp, b) * math.comb(bmax - bp, bp - b))
            p[rang] = p.get(rang, 0.0) + cas / total
    return p


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_p_any_win_coherent_avec_la_table(cfg):
    """La probabilité de gagner quelque chose, recomposée rang par rang,
    doit retomber sur le p_any_win de la config (qui sert à estimer la
    participation, donc toute l'EV).

    Tolérance serrée : « 1 chance sur 6 » et « 1 sur 13 » sont les arrondis
    commerciaux de la FDJ, pas les vraies valeurs (1/5,985 et 1/12,974).
    Les employer comme diviseur de `n_est` biaise l'estimation de la
    participation, donc `ev_fixe`. Le moteur doit utiliser la combinatoire
    exacte et laisser les arrondis à la communication.
    """
    assert sum(proba_par_rang(cfg).values()) == pytest.approx(
        cfg["p_any_win"], rel=1e-9)


@pytest.mark.parametrize(
    ("cle", "attendu"),
    [("loto", 3_185_973 / 19_068_840),
     ("euromillions", 10_778_691 / 139_838_160)],
    ids=IDS)
def test_p_any_win_est_la_valeur_combinatoire_exacte(cle, attendu):
    """Ancrage sur un dénombrement fait à la main, hors du moteur.

    Loto : (142 121 × 10 + 1 764 763) / 19 068 840 = 0,1670772
      · 142 121 = tirages ayant ≥ 2 numéros en commun avec une grille
      · 1 764 763 = tirages en ayant ≤ 1 (gagnants par le seul n° Chance)
    EuroMillions : (152 026 × 66 + 744 975) / 139 838 160 = 0,0770792
    """
    assert JEUX[cle]["p_any_win"] == pytest.approx(attendu, rel=1e-12)


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_nombre_de_rangs_officiels(cfg):
    attendu = 9 if cfg["bonus_pick"] == 1 else 13
    assert sorted(proba_par_rang(cfg)) == list(range(1, attendu + 1))


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_rangs_ordonnes_du_plus_rare_au_plus_frequent(cfg):
    """Rang 1 = le plus improbable, rang N = le plus courant.

    Tolérance de 5 % : la FDJ ordonne ses rangs par MONTANT de gain, pas
    strictement par probabilité. À l'EuroMillions, 3+2 (rang 6) est ainsi
    2 % MOINS probable que 4+0 (rang 7) tout en étant mieux payé — c'est le
    montant qui fait l'ordre, pas la rareté. Au-delà de cette marge, une
    inversion signalerait une vraie erreur de table.
    """
    p = proba_par_rang(cfg)
    probas = [p[r] for r in sorted(p)]
    for precedent, suivant in zip(probas, probas[1:], strict=False):
        assert precedent <= suivant * 1.05, "inversion de rangs trop large"


def test_euromillions_rang_6_est_3plus2_et_rang_7_est_4plus0():
    """L'ordre officiel EuroMillions suit le MONTANT décroissant : 3+2 est
    mieux payé que 4+0, donc 3+2 = rang 6 et 4+0 = rang 7.

    Le piège : 4+0 est un peu PLUS probable que 3+2. Raisonner sur la rareté
    au lieu du montant intervertit les deux rangs — une erreur invisible
    (rien ne plante) qui paie un 4+0 au tarif d'un 3+2. Le rapport réel des
    tirages FDJ tranche : cf. test_ordre_des_rangs_conforme_aux_rapports_fdj.
    """
    em = JEUX["euromillions"]
    assert rang_gagne(em, 3, 2) == 6
    assert rang_gagne(em, 4, 0) == 7
    p = proba_par_rang(em)
    assert p[6] < p[7]                       # 3+2 un peu plus rare que 4+0
    assert p[7] / p[6] == pytest.approx(1.023, abs=0.01)


@pytest.mark.parametrize("cle", ["loto", "euromillions"], ids=IDS)
def test_ordre_des_rangs_conforme_aux_rapports_fdj(cle):
    """L'ancrage EXTERNE de la table : les rapports réellement publiés.

    La FDJ classe ses rangs par montant décroissant. Donc le rapport médian
    du rang k doit être supérieur à celui du rang k+1, sur les vrais tirages.
    Aucune hypothèse du moteur n'entre ici : seulement le CSV FDJ. Si
    `rang_gagne` intervertit deux rangs, la table du moteur cesse de décrire
    l'échelle de gains qu'elle prétend décrire — et ce test le voit.

    Lit les archives `data/*.zip` versionnées avec le dépôt (le test reste
    donc hors ligne) : les fixtures ne comptent que 6 tirages, trop peu pour
    départager deux rangs voisins.
    """
    cfg = JEUX[cle]
    tirages = tirages_des_archives(cle)
    assert len(tirages) > 200, "archives trop courtes pour être concluantes"

    n_rangs = 9 if cfg["bonus_pick"] == 1 else 13
    medians = {}
    for rang in range(1, n_rangs + 1):
        vals = [t["rapports"][rang] for t in tirages
                if t["rapports"].get(rang)]
        if vals:
            medians[rang] = statistics.median(vals)

    for rang in sorted(medians):
        suivant = rang + 1
        if suivant not in medians:
            continue
        assert medians[rang] > medians[suivant], (
            f"rang {rang} ({medians[rang]:.2f} €) devrait payer plus que "
            f"le rang {suivant} ({medians[suivant]:.2f} €) — table erronée")


def test_euromillions_3plus2_est_mieux_paye_que_4plus0():
    """Le cas précis qui distingue la bonne table de la mauvaise, tranché
    par les rapports FDJ réels et non par un raisonnement sur la rareté."""
    cfg = JEUX["euromillions"]
    tirages = tirages_des_archives("euromillions")
    rang_3p2 = rang_gagne(cfg, 3, 2)
    rang_4p0 = rang_gagne(cfg, 4, 0)
    med = {r: statistics.median([t["rapports"][r] for t in tirages
                                 if t["rapports"].get(r)])
           for r in (rang_3p2, rang_4p0)}
    assert med[rang_3p2] > med[rang_4p0], (
        f"3+2 doit être mieux payé que 4+0 : le moteur les place aux rangs "
        f"{rang_3p2} et {rang_4p0}, payés {med[rang_3p2]:.2f} € et "
        f"{med[rang_4p0]:.2f} €")


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_jackpot_est_le_rang_1(cfg):
    assert rang_gagne(cfg, cfg["pick"], cfg["bonus_pick"]) == 1


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_grille_perdante_ne_gagne_rien(cfg):
    assert rang_gagne(cfg, 0, 0) is None
    assert rang_gagne(cfg, 1, 0) is None


def test_loto_numero_chance_seul_gagne_le_rang_9():
    """Particularité Loto : 0 ou 1 bon numéro + le n° Chance = rang 9."""
    loto = JEUX["loto"]
    assert rang_gagne(loto, 0, 1) == 9
    assert rang_gagne(loto, 1, 1) == 9
    assert rang_gagne(loto, 2, 1) == 7          # au-delà, c'est un vrai rang


def test_euromillions_une_boule_deux_etoiles_gagne():
    assert rang_gagne(JEUX["euromillions"], 1, 2) == 11
    assert rang_gagne(JEUX["euromillions"], 1, 1) is None


def test_reglement_credite_le_bon_rapport():
    """Bout en bout : une grille réglée contre un tirage réel doit toucher
    le rapport du rang correspondant, et rien d'autre."""
    cfg = JEUX["loto"]
    tirage = {"balls": (5, 25, 37, 41, 42), "bonus": (10,),
              "rapports": {1: 6_000_000.0, 5: 500.0, 6: 25.0, 9: 2.20}}
    # 3 bons + le n° Chance → rang 5
    r = regler_grille(cfg, {"numeros": [5, 25, 37, 1, 2], "bonus": [10]}, tirage)
    assert r["rang"] == 5 and r["gain"] == pytest.approx(500.0)
    assert r["bons"] == [5, 25, 37] and r["bonus_ok"] == [10]
    # 3 bons sans le n° Chance → rang 6
    r = regler_grille(cfg, {"numeros": [5, 25, 37, 1, 2], "bonus": [3]}, tirage)
    assert r["rang"] == 6 and r["gain"] == pytest.approx(25.0)
    # rien du tout
    r = regler_grille(cfg, {"numeros": [1, 2, 3, 4, 6], "bonus": [3]}, tirage)
    assert r["rang"] is None and r["gain"] == 0.0


def test_reglement_sans_rapport_publie_ne_credite_rien():
    """Rang atteint mais rapport absent du CSV : on crédite 0, jamais None
    (sinon le cumul du grand livre planterait)."""
    cfg = JEUX["loto"]
    tirage = {"balls": (5, 25, 37, 41, 42), "bonus": (10,), "rapports": {}}
    r = regler_grille(cfg, {"numeros": [5, 25, 37, 41, 42], "bonus": [10]},
                      tirage)
    assert r["rang"] == 1 and r["gain"] == 0.0
