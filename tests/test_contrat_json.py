"""Le contrat JSON consommé par docs/index.html (et demain par l'app Next.js).

Ces tests lisent les exports RÉELS commités dans docs/. Ils échouent si une
évolution du moteur casse la page sans bump de `meta.version`.

Ils vérifient aussi que les garde-fous du produit survivent à l'export : le
backtest présent, l'EV honnête (négative), le grand livre non masqué.
"""
from __future__ import annotations

import json

import pytest
from conftest import RACINE

from oracle import JEUX

EXPORTS = ["loto", "euromillions"]


@pytest.fixture(scope="module", params=EXPORTS)
def export(request):
    chemin = RACINE / "docs" / f"{request.param}.json"
    if not chemin.exists():
        pytest.skip(f"{chemin.name} absent — lance oracle.py --export-web")
    return json.loads(chemin.read_text(encoding="utf-8"))


def test_cles_racine(export):
    attendues = {"meta", "dernier_tirage", "modes", "techniques", "ev_params",
                 "calibration", "systeme", "verdicts"}
    assert attendues <= set(export)


def test_meta_complet(export):
    m = export["meta"]
    for cle in ("version", "source", "jeu", "nom", "bonus_nom", "bonus_pick",
                "bonus_max", "n_max", "prix", "proba_jackpot", "page_jeu",
                "prochain_tirage", "prochain_jour", "n_tirages",
                "periode_debut", "periode_fin", "genere_le"):
        assert cle in m, cle
    assert m["version"] == "2.2"
    assert m["source"] == "fdj", "export encore basé sur des données de démo"


def test_meta_v22_traçabilite(export):
    """Nouveautés 2.2 : d'où viennent les données, et ce qui cloche."""
    m = export["meta"]
    assert isinstance(m["archives"], list) and m["archives"]
    # ordre chronologique des époques, tel que déclaré dans JEUX[...]["archives"]
    declare = [a["label"] for a in JEUX[m["jeu"]]["archives"]]
    assert m["archives"] == [x for x in declare if x in m["archives"]]
    assert isinstance(m["alertes"], list)
    for a in m["alertes"]:
        assert a["niveau"] in ("info", "attention", "critique")
        assert a["message"].strip()
    assert isinstance(m["epoques_exclues"], dict)


def test_pas_d_alerte_critique_dans_un_export_publie(export):
    critiques = [a for a in export["meta"]["alertes"]
                 if a["niveau"] == "critique"]
    assert not critiques, critiques


def test_periode_coherente(export):
    m = export["meta"]
    assert m["periode_debut"] < m["periode_fin"] < m["prochain_tirage"], \
        "le prochain tirage doit être postérieur au dernier tirage connu"


def test_modes_et_grilles(export):
    m = export["meta"]
    cfg = JEUX[m["jeu"]]
    for mode in ("hybride", "pronostic", "anti"):
        bloc = export["modes"][mode]
        assert len(bloc["scores"]) == m["n_max"]
        assert sorted(bloc["classement"]) == list(range(1, m["n_max"] + 1))
        assert len(bloc["bonus"]) == m["bonus_max"]
        assert bloc["grilles"]
        for g in bloc["grilles"]:
            assert len(g["numeros"]) == cfg["pick"]
            assert len(set(g["numeros"])) == cfg["pick"]
            assert all(1 <= n <= m["n_max"] for n in g["numeros"])
            assert len(g["bonus"]) == m["bonus_pick"]
            assert all(1 <= b <= m["bonus_max"] for b in g["bonus"])
            assert g["pop_rel"] > 0


def test_calibration_exportee(export):
    cal = export["calibration"]
    assert len(cal["beta"]) == export["meta"]["n_max"]
    assert len(cal["top_surjoues"]) and len(cal["top_delaisses"])
    assert set(cal["top_surjoues"]).isdisjoint(cal["top_delaisses"])


def test_ev_params_permet_le_recalcul_live(export):
    """La page recalcule l'EV quand on change le jackpot : elle a besoin de
    ces quatre valeurs, et d'aucune autre."""
    e = export["ev_params"]
    assert e["n_est"] > 0
    assert e["p_jackpot_inv"] == export["meta"]["proba_jackpot"]
    assert e["prix"] == export["meta"]["prix"]
    assert e["ev_fixe"] is None or 0 <= e["ev_fixe"] < e["prix"]


# ---- garde-fous produit (section 2 du MASTERPROMPT) -------------------------

def test_le_backtest_est_toujours_exporte(export):
    """Garde-fou n°1 : le juge de paix ne sort jamais du produit."""
    bt = export["verdicts"]["backtest"]
    assert bt and bt["n_tests"] > 100
    for cle in ("modele", "aleatoire", "theorique", "froid"):
        assert cle in bt


def test_le_folklore_ne_bat_pas_le_hasard(export):
    """Sur données réelles, l'écart modèle/théorique doit rester du bruit.
    Si ce test rougit un jour, c'est l'intégrité des données qu'il faut
    suspecter avant de crier à la découverte."""
    bt = export["verdicts"]["backtest"]
    ecart = abs(bt["modele"] - bt["theorique"])
    seuil = 2 * (0.45 / bt["n_tests"]) ** 0.5
    assert ecart <= 1.5 * seuil, f"écart {ecart:+.4f} hors bruit (seuil {seuil:.4f})"


def test_chi2_et_effet_anniversaire_presents(export):
    assert export["verdicts"]["chi2"]["ddl"] == export["meta"]["n_max"] - 1
    assert export["verdicts"]["effet_anniversaire"]["n"] > 0


def test_l_ev_reste_negative(export):
    """Garde-fou n°5 : l'EV affichée est honnête. On la recalcule ici comme
    le fait la page, au jackpot exporté."""
    e, m = export["ev_params"], export["meta"]
    jackpot = m["jackpot"] or 0
    p1 = 1 / e["p_jackpot_inv"]
    for g in export["modes"]["anti"]["grilles"]:
        partage = e["n_est"] * p1 * g["pop_rel"]
        ev = (e["ev_fixe"] or 0) + p1 * jackpot / (1 + partage) - e["prix"]
        assert ev < 0, "EV positive affichée — vérifier avant de publier"


def test_le_grand_livre_n_est_pas_masque(export):
    """Garde-fou v2.1 : le compteur misé/gagné/ROI reste exporté."""
    assert "historique" in export
    h = export["historique"]
    assert h is not None and "cumul" in h and "en_attente" in h
