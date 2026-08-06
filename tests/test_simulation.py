"""La rétro-simulation en euros : ce qu'elle mesure, et à quel prix.

`retro_simulation` répond à « combien j'aurais récupéré si j'avais joué ».
Deux façons de mentir sans rien casser :

  · miser au mauvais prix — le Loto coûtait 2,00 € avant le 04/11/2019, pas
    2,20 € ; appliquer le tarif d'aujourd'hui à hier gonfle la mise, donc
    dégrade le ROI affiché ;
  · additionner des gains qui ne recomposent pas le total détaillé.
"""
from __future__ import annotations

from datetime import date

import pytest
from test_rangs import tirages_des_archives

from oracle import JEUX, prix_du_tirage, retro_simulation

TOUS = [JEUX["loto"], JEUX["euromillions"]]
IDS = ["loto", "euromillions"]


def test_le_loto_coutait_2_euros_avant_novembre_2019():
    """Le « nouveau Loto » du 04/11/2019 a porté la grille de 2,00 à 2,20 €."""
    loto = JEUX["loto"]
    assert prix_du_tirage(loto, date(2019, 11, 3)) == pytest.approx(2.00)
    assert prix_du_tirage(loto, date(2019, 11, 4)) == pytest.approx(2.20)
    assert prix_du_tirage(loto, date(2017, 6, 1)) == pytest.approx(2.00)
    assert prix_du_tirage(loto, date(2026, 1, 1)) == pytest.approx(2.20)


def test_euromillions_garde_son_tarif():
    em = JEUX["euromillions"]
    for d in (date(2016, 10, 1), date(2026, 1, 1)):
        assert prix_du_tirage(em, d) == pytest.approx(2.50)


def test_la_mise_simulee_utilise_le_prix_de_l_epoque():
    """Sur toute la profondeur des archives, la mise cumulée doit valoir la
    somme des prix RÉELS, pas le tarif courant × nombre de tirages."""
    cfg = JEUX["loto"]
    tirages = tirages_des_archives("loto")
    # fenêtre courte MAIS à cheval sur le 04/11/2019 : c'est la seule
    # propriété dont le test a besoin, et le pipeline complet est lent.
    bascule = next(i for i, x in enumerate(tirages)
                   if x["date"] >= date(2019, 11, 4))
    tirages = tirages[:bascule + 10]
    sim = retro_simulation(cfg, tirages, 20, iters=400)
    joues = tirages[-sim["n_tirages"]:]
    attendu = round(sum(prix_du_tirage(cfg, t["date"]) for t in joues), 2)
    forfait = round(cfg["prix"] * len(joues), 2)
    assert attendu < forfait, "la fenêtre doit couvrir l'époque à 2,00 €"
    for mode, v in sim["modes"].items():
        assert v["mise"] == pytest.approx(attendu, abs=0.01), (
            f"{mode} : mise {v['mise']:.2f} € au lieu de {attendu:.2f} € "
            f"(tarif forfaitaire = {forfait:.2f} €)")


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_le_detail_recompose_le_total_au_centime(cfg):
    """Sans cette égalité, le total « récupéré » reste à croire sur parole."""
    tirages = tirages_des_archives(cfg["nom"].lower())
    sim = retro_simulation(cfg, tirages, 25, iters=400)
    dates_reelles = {t["date"].isoformat() for t in tirages}
    for mode, v in sim["modes"].items():
        somme = round(sum(g["gain"] for g in v["gains"]), 2)
        assert somme == pytest.approx(v["gain"], abs=0.005), (
            f"{mode} : le détail somme à {somme:.2f} € pour un total "
            f"affiché de {v['gain']:.2f} €")
        assert len(v["gains"]) == v["n_gains"]
        for g in v["gains"]:
            assert g["date"] in dates_reelles, (
                f"{mode} : gain daté du {g['date']}, absent des tirages réels")


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_l_historique_complet_couvre_chaque_tirage_simule(cfg):
    """v2.7 — la page montre l'historique complet : chaque tirage rejoué a sa
    ligne, perdant compris, et l'ensemble recompose mise et gains."""
    tirages = tirages_des_archives(cfg["nom"].lower())
    sim = retro_simulation(cfg, tirages, 25, iters=400)
    for mode, v in sim["modes"].items():
        lignes = v["tirages"]
        assert len(lignes) == sim["n_tirages"], (
            f"{mode} : {len(lignes)} lignes pour {sim['n_tirages']} tirages")
        assert lignes == sorted(lignes, key=lambda g: g["date"], reverse=True)
        assert round(sum(g["gain"] for g in lignes), 2) == pytest.approx(
            v["gain"], abs=0.005)
        assert sum(1 for g in lignes if g["rang"]) == v["n_gains"]
        for g in lignes:
            assert len(g["bonus_sortis"]) == cfg["bonus_pick"]
            if g["rang"] is None:
                assert g["gain"] == 0
