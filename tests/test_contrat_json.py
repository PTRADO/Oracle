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
    assert m["version"] == "2.3"
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
    # Tirage sans remise : E[χ²] = n_max − pick (et non n_max − 1), la loi de
    # référence étant (1 − pick/n_max)·χ²(n_max). Cf. tests/test_chi2.py.
    c2 = export["verdicts"]["chi2"]
    assert c2["ddl"] == export["meta"]["n_max"]
    assert c2["esperance"] == export["meta"]["n_max"] - 5
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


# ---- v2.3 : la recherche de formule et l'historique jouable ----------------

def test_la_recherche_de_formule_est_exportee(export):
    """La page en fait sa section « Est-ce que ça marche ? » : sans elle,
    l'argument central du produit disparaît de l'écran."""
    rc = export["verdicts"]["recherche"]
    assert rc, "recherche absente — relancer avec --recherche"
    for cle in ("theorique", "reel", "nul", "z_vs_nul", "p_empirique",
                "verdict", "budget_par_recherche", "n_tirages_evalues"):
        assert cle in rc, cle
    assert rc["budget_par_recherche"] >= 100
    assert rc["nul"]["n_essais"] >= 5, "trop peu de témoins pour conclure"


def test_la_recherche_ne_conclut_pas_a_un_signal(export):
    """Garde-fou n°2 sur le résultat publié : tant qu'aucun signal n'est
    établi, la page ne doit pas laisser croire le contraire."""
    rc = export["verdicts"]["recherche"]
    assert rc["p_empirique"] > 0.05, (
        "p ≤ 0,05 : à vérifier manuellement (intégrité des données, autre "
        "période) AVANT de publier quoi que ce soit")
    assert "Aucun signal" in rc["verdict"]


def test_la_formule_s_effondre_hors_echantillon(export):
    """Le fait pédagogique central : la formule brille sur ce qu'elle a vu,
    puis retombe. Si ce n'était plus vrai, il faudrait le regarder de près."""
    r = export["verdicts"]["recherche"]["reel"]
    assert r["score_entrainement"] > r["score_validation"]


def test_l_historique_jouable_est_exporte(export):
    """La section « si tu avais joué » a besoin de ces champs."""
    sim = export["simulation"]
    assert sim, "simulation absente — relancer avec --simulation"
    assert sim["n_tirages"] >= 50
    a = sim["modes"]["anti"]
    for cle in ("mise", "gain", "n_gains", "meilleur_gain", "roi_pct", "gains"):
        assert cle in a, cle
    assert a["mise"] > a["gain"], "un ROI positif sur 100 tirages doit alerter"
    assert 0 <= a["n_gains"] <= sim["n_tirages"]


def test_chaque_gain_est_tracable_a_un_tirage_reel(export):
    """Le total « récupéré » ne doit pas être un chiffre à croire sur parole :
    chaque euro s'explique par un tirage, un rang officiel et un rapport FDJ."""
    a = export["simulation"]["modes"]["anti"]
    cfg = JEUX[export["meta"]["jeu"]]
    gains = a["gains"]
    assert len(gains) == a["n_gains"]
    total = 0.0
    for g in gains:
        for cle in ("date", "grille", "bonus", "sortis", "bons", "bonus_ok",
                    "rang", "gain"):
            assert cle in g, cle
        assert g["gain"] > 0
        assert 1 <= g["rang"] <= (9 if cfg["bonus_pick"] == 1 else 13)
        # les bons numéros sont bien l'intersection annoncée
        assert set(g["bons"]) == set(g["grille"]) & set(g["sortis"])
        assert len(g["grille"]) == cfg["pick"]
        total += g["gain"]
    assert abs(total - a["gain"]) < 0.02, "le détail ne recompose pas le total"
    if gains:
        assert gains[0]["gain"] == a["meilleur_gain"], "gains non triés"


def test_la_simulation_joue_les_grilles_vraiment_publiees(export):
    """v2.4 — la rétro-simulation jouait un raccourci (top-5 des scores) tout
    en affirmant mesurer le ROI réel. L'audit a montré que ces grilles
    diffèrent des grilles publiées dans 92 à 100 % des cas. Elle rejoue
    désormais le pipeline de publication.

    Preuve dans l'export : les grilles simulées respectent les contraintes de
    forme de `generer_grilles` (parité, dizaines, pas de suite de 3), que le
    top-5 des scores ne respectait pas.
    """
    sim = export["simulation"]
    assert "top-5" not in sim["note"] and "simplifié" not in sim["note"]
    gains = sim["modes"]["anti"]["gains"]
    if not gains:
        pytest.skip("aucun gain sur la période")
    for g in gains:
        b = sorted(g["grille"])
        pairs = sum(1 for x in b if x % 2 == 0)
        assert pairs in (2, 3), f"{b} : parité hors contrainte"
        assert len({(x - 1) // 10 for x in b}) >= 3, f"{b} : moins de 3 dizaines"
        suite = 1
        for k in range(1, len(b)):
            suite = suite + 1 if b[k] == b[k - 1] + 1 else 1
            assert suite < 3, f"{b} : 3 numéros consécutifs"
