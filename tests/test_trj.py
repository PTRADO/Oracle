"""Le taux de retour joueur, décomposé — la vérité que ce projet existe pour dire.

La FDJ annonce « environ 50 % reversés aux joueurs ». C'est exact au niveau
du jeu, et trompeur au niveau du joueur : la moitié de cette somme part dans
les rangs très élevés — au Loto, près d'un tiers dans le seul rang 1 — qu'un
joueur d'un ticket par tirage ne touchera jamais de sa vie.

Ce que ce joueur récupère vraiment, c'est `ev_fixe / prix`. Ces tests
vérifient que le moteur mesure cette décomposition et qu'elle est cohérente
avec les rapports FDJ réels.
"""
from __future__ import annotations

import pytest
from test_rangs import tirages_des_archives

from oracle import JEUX, decomposition_trj, parametres_ev

IDS = ["loto", "euromillions"]


@pytest.fixture(scope="module")
def archives():
    return {cle: tirages_des_archives(cle) for cle in IDS}


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_trj_hors_jackpot_est_publie(cle, archives):
    """Le moteur doit exposer le chiffre, pas seulement l'euro par grille."""
    cfg = JEUX[cle]
    ev = parametres_ev(cfg, archives[cle])
    # abs=1e-4 : le moteur arrondit ses ratios à 4 décimales.
    assert ev["trj_hors_jackpot_recent"] == pytest.approx(
        ev["ev_fixe"] / cfg["prix"], abs=1e-4)
    assert 0.10 < ev["trj_hors_jackpot_recent"] < 0.45, (
        "un joueur d'un ticket récupère une fraction du prix, très loin "
        "des 50 % annoncés pour le jeu entier")


def test_le_loto_rend_environ_50pct_au_total_mais_35pct_hors_jackpot(archives):
    """Ancrage EXTERNE : recomposé depuis les rapports et les nombres de
    gagnants FDJ, le TRJ total du Loto doit retomber sur le ~50 % publié.

    C'est ce qui rend la seconde moitié du test crédible : le même calcul,
    privé du rang 1, tombe autour de 35 %. L'écart n'est pas une erreur de
    mesure — c'est la part qui part dans le jackpot.
    """
    d = decomposition_trj(JEUX["loto"], archives["loto"])
    assert d["trj_total"] == pytest.approx(0.50, abs=0.03), (
        f"TRJ total recomposé {d['trj_total']:.1%}, incohérent avec le ~50 % "
        "annoncé par la FDJ — la mesure ou les données sont fausses")
    assert d["trj_hors_jackpot"] == pytest.approx(0.35, abs=0.03)
    assert d["part_jackpot"] > 0.20


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_la_decomposition_est_coherente(cle, archives):
    d = decomposition_trj(JEUX[cle], archives[cle])
    assert d["trj_hors_jackpot"] < d["trj_total"]
    assert d["part_jackpot"] == pytest.approx(
        1 - d["trj_hors_jackpot"] / d["trj_total"], abs=1e-4)
    assert d["n_tirages"] > 900


def test_le_jackpot_pese_plus_lourd_a_l_euromillions(archives):
    """L'EuroMillions concentre bien davantage la cagnotte sur le rang 1 :
    c'est l'explication chiffrée de son retour hors-jackpot deux fois plus
    faible que celui du Loto (≈18 % contre ≈35 %)."""
    loto = decomposition_trj(JEUX["loto"], archives["loto"])
    em = decomposition_trj(JEUX["euromillions"], archives["euromillions"])
    assert em["part_jackpot"] > loto["part_jackpot"]
    assert em["trj_hors_jackpot"] < loto["trj_hors_jackpot"]


def test_les_deux_trj_hors_jackpot_ne_portent_pas_le_meme_nom(archives):
    """Deux mesures voisines coexistent : l'une sur les 160 derniers tirages
    (`ev_params`), l'autre sur tout l'historique (`verdicts.trj`). Les nommer
    pareil dans le même export piégeait quiconque consomme le contrat."""
    cfg = JEUX["loto"]
    ev = parametres_ev(cfg, archives["loto"])
    d = decomposition_trj(cfg, archives["loto"])
    assert "trj_hors_jackpot" not in ev
    assert "trj_hors_jackpot_recent" in ev
    assert "trj_hors_jackpot" in d
    # elles restent du même ordre, sinon l'une des deux est fausse
    assert abs(ev["trj_hors_jackpot_recent"] - d["trj_hors_jackpot"]) < 0.06


def test_le_trj_facture_le_passe_au_tarif_de_l_epoque(archives):
    """417 tirages Loto valaient 2,00 €. Les compter à 2,20 € gonfle les
    mises et sous-estime le TRJ de ~1,3 point."""
    cfg = JEUX["loto"]
    tirages = archives["loto"]
    d = decomposition_trj(cfg, tirages)

    mises_forfait = tot = 0.0
    for t in tirages:
        g = sum(t["gagnants"].values())
        if g <= 0 or not t["rapports"]:
            continue
        mises_forfait += (g / cfg["p_any_win"]) * cfg["prix"]
        tot += sum(t["gagnants"].get(r, 0) * rap
                   for r, rap in t["rapports"].items())
    trj_forfait = tot / mises_forfait
    assert d["trj_total"] > trj_forfait + 0.005, (
        f"le TRJ publié ({d['trj_total']:.4f}) devrait dépasser la version "
        f"au tarif forfaitaire ({trj_forfait:.4f})")
