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

import pytest

from oracle import JEUX, rang_gagne, regler_grille

TOUS = [JEUX["loto"], JEUX["euromillions"]]
IDS = ["loto", "euromillions"]


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
    participation, donc toute l'EV)."""
    assert sum(proba_par_rang(cfg).values()) == pytest.approx(
        cfg["p_any_win"], rel=0.01)


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_nombre_de_rangs_officiels(cfg):
    attendu = 9 if cfg["bonus_pick"] == 1 else 13
    assert sorted(proba_par_rang(cfg)) == list(range(1, attendu + 1))


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_rangs_ordonnes_du_plus_rare_au_plus_frequent(cfg):
    """Rang 1 = le plus improbable, rang N = le plus courant.

    Tolérance de 5 % : la FDJ ordonne ses rangs par MONTANT de gain, pas
    strictement par probabilité. À l'EuroMillions, 4+0 (rang 6) est ainsi
    2 % plus probable que 3+2 (rang 7) — inversion officielle, vérifiée
    ci-dessous. Au-delà de cette marge, une inversion signalerait une vraie
    erreur de table.
    """
    p = proba_par_rang(cfg)
    probas = [p[r] for r in sorted(p)]
    for precedent, suivant in zip(probas, probas[1:], strict=False):
        assert precedent <= suivant * 1.05, "inversion de rangs trop large"


def test_inversion_officielle_euromillions_rangs_6_et_7():
    """Verrouille l'anomalie ci-dessus pour qu'elle reste un choix documenté
    et non une régression silencieuse."""
    p = proba_par_rang(JEUX["euromillions"])
    assert rang_gagne(JEUX["euromillions"], 4, 0) == 6
    assert rang_gagne(JEUX["euromillions"], 3, 2) == 7
    assert p[6] > p[7]                       # 4+0 un peu plus probable que 3+2
    assert p[6] / p[7] == pytest.approx(1.023, abs=0.01)


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
