"""Tests du chercheur de formule (`recherche.py`).

Le test central est `test_chercheur_retrouve_un_signal_plante` : sans lui, le
résultat négatif obtenu sur les vraies données FDJ ne vaudrait rien — il
pourrait signifier « le chercheur est aveugle » plutôt que « il n'y a rien à
voir ». On lui plante donc un biais connu et on exige qu'il le retrouve HORS
ÉCHANTILLON.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import pytest
from conftest import RACINE  # noqa: F401  (règle sys.path)

import recherche as R
from oracle import JEUX

LOTO = JEUX["loto"]
THEO = LOTO["pick"] ** 2 / LOTO["n_max"]      # 0.5102 bon numéro par tirage


def tirages_synthetiques(n, rng, favoris=(), force=1.0, cfg=LOTO):
    """Fabrique n tirages. `force` > 1 truque l'urne en faveur de `favoris`."""
    N = list(range(1, cfg["n_max"] + 1))
    poids = [force if x in favoris else 1.0 for x in N]
    out, d = [], date(2015, 1, 5)
    for _ in range(n):
        b = set()
        while len(b) < cfg["pick"]:
            b.add(rng.choices(N, weights=poids)[0])
        out.append({"date": d, "jour": d.weekday(), "balls": tuple(sorted(b)),
                    "bonus": (1,), "gagnants": {}, "rapports": {}})
        d += timedelta(days=3)
    return out


# ---- le test qui rend crédible tout le reste ---------------------------------

def test_chercheur_retrouve_un_signal_plante():
    """Urne truquée : 5 numéros sortent 2,5× plus souvent. Le chercheur doit
    le voir, et le gain doit tenir sur des tirages qu'il n'a jamais vus."""
    rng = random.Random(1)
    T = tirages_synthetiques(700, rng, favoris={3, 17, 28, 41, 44}, force=2.5)
    X, cibles = R.construire(LOTO, T)
    res = R.chercher(X, cibles, LOTO["pick"], random.Random(2),
                     budget=60, departs=2)
    assert res["score_validation"] > THEO + 0.15, (
        f"chercheur aveugle : {res['score_validation']:.3f} vs {THEO:.3f} "
        "attendu sous hasard — un résultat négatif sur données réelles ne "
        "vaudrait alors rien")


def test_chercheur_ne_trouve_rien_dans_du_bruit():
    """Urne parfaitement équitable : hors échantillon, on doit retomber sur
    l'espérance du hasard. C'est le contrôle symétrique du précédent."""
    rng = random.Random(3)
    T = tirages_synthetiques(700, rng)
    X, cibles = R.construire(LOTO, T)
    res = R.chercher(X, cibles, LOTO["pick"], random.Random(4),
                     budget=60, departs=2)
    assert abs(res["score_validation"] - THEO) < 0.20
    # …alors même que l'entraînement, lui, brille : c'est le sur-apprentissage.
    assert res["score_entrainement"] > res["score_validation"]


def test_l_illusion_d_entrainement_existe_aussi_sur_du_bruit():
    """Le cœur du protocole : sur des données SANS structure, la recherche
    fabrique quand même un gain d'entraînement. C'est pourquoi comparer au
    hasard ne suffit pas — il faut comparer au témoin."""
    rng = random.Random(5)
    T = tirages_synthetiques(600, rng)
    X, cibles = R.construire(LOTO, T)
    res = R.chercher(X, cibles, LOTO["pick"], random.Random(6),
                     budget=60, departs=2)
    assert res["score_entrainement"] > THEO + 0.03


# ---- correction du calcul des traits ----------------------------------------

def test_les_traits_sont_calcules_en_aveugle():
    """Aucun trait du tirage t ne doit dépendre du tirage t lui-même.
    Sans cette propriété, tous les scores seraient faux par fuite du futur."""
    rng = random.Random(7)
    T = tirages_synthetiques(200, rng)
    X1, _ = R.construire(LOTO, T)

    modifie = [dict(t) for t in T]
    modifie[150]["balls"] = (1, 2, 3, 4, 5)          # on change le tirage 150
    X2, _ = R.construire(LOTO, modifie)

    idx = 150 - 60                                    # position dans X
    assert X1[idx] == X2[idx], "fuite : le tirage t influence ses propres traits"
    assert X1[idx - 1] == X2[idx - 1]
    assert X1[idx + 1] != X2[idx + 1], "le tirage t doit influencer les suivants"


def test_forme_de_la_matrice_des_traits():
    rng = random.Random(8)
    T = tirages_synthetiques(150, rng)
    X, cibles = R.construire(LOTO, T)
    assert len(X) == len(cibles) == 150 - 60
    for traits in X:
        assert len(traits) == R.N_TRAITS
        for colonne in traits:
            assert len(colonne) == LOTO["n_max"]


def test_traits_centres_reduits():
    """Chaque trait est ramené à moyenne 0 / écart-type 1 sur les numéros,
    sinon un trait à grandes valeurs écraserait les autres."""
    rng = random.Random(9)
    X, _ = R.construire(LOTO, tirages_synthetiques(150, rng))
    for colonne in X[-1]:
        moy = sum(colonne) / len(colonne)
        assert abs(moy) < 1e-9
        ecart = (sum((v - moy) ** 2 for v in colonne) / len(colonne)) ** 0.5
        assert abs(ecart - 1.0) < 1e-9 or ecart == 0.0


def test_zscore_cas_degenere():
    assert R._zscore([4.0] * 10) == [0.0] * 10       # aucune variance


# ---- évaluation --------------------------------------------------------------

def test_evaluer_poids_nuls_donne_le_hasard():
    """Poids tous nuls : le classement est arbitraire mais fixe, donc le score
    doit rester dans le voisinage de l'espérance du hasard."""
    rng = random.Random(10)
    X, cibles = R.construire(LOTO, tirages_synthetiques(500, rng))
    s = R.evaluer(X, cibles, [0.0] * R.N_TRAITS, LOTO["pick"])
    assert abs(s - THEO) < 0.25


def test_evaluer_borne_par_le_nombre_de_boules():
    rng = random.Random(11)
    X, cibles = R.construire(LOTO, tirages_synthetiques(200, rng))
    poids = [1.0] + [0.0] * (R.N_TRAITS - 1)
    assert 0.0 <= R.evaluer(X, cibles, poids, LOTO["pick"]) <= LOTO["pick"]


def test_evaluer_refuse_un_vecteur_de_mauvaise_taille():
    rng = random.Random(12)
    X, cibles = R.construire(LOTO, tirages_synthetiques(120, rng))
    with pytest.raises(ValueError):
        R.evaluer(X, cibles, [1.0, 2.0], LOTO["pick"])


# ---- modèle nul --------------------------------------------------------------

def test_permuter_conserve_les_tirages_et_detruit_l_ordre():
    rng = random.Random(13)
    T = tirages_synthetiques(300, rng)
    P = R.permuter(T, random.Random(14))
    assert sorted(t["balls"] for t in T) == sorted(t["balls"] for t in P)
    assert [t["date"] for t in T] == [t["date"] for t in P]
    assert [t["balls"] for t in T] != [t["balls"] for t in P]


def test_etude_complete_rend_un_verdict_exploitable():
    rng = random.Random(15)
    T = tirages_synthetiques(400, rng)
    res = R.etude_complete(LOTO, T, random.Random(16), budget=40, n_nuls=3, departs=1)
    assert res is not None
    for cle in ("n_tirages_evalues", "theorique", "reel", "nul",
                "z_vs_nul", "p_empirique", "verdict", "budget_par_recherche"):
        assert cle in res
    assert res["reel"]["n_entrainement"] + res["reel"]["n_validation"] \
        == res["n_tirages_evalues"]
    assert res["verdict"].strip()
    assert isinstance(res["reel"]["poids"], dict)


def test_p_empirique_bornee_et_coherente():
    """La p-valeur ne peut jamais valoir 0 (aucun échantillon fini ne le
    permet) et doit refléter le décompte des témoins aussi bons."""
    rng = random.Random(23)
    res = R.etude_complete(LOTO, tirages_synthetiques(400, rng),
                           random.Random(24), budget=40, n_nuls=4, departs=1)
    n = res["nul"]
    assert 0 < res["p_empirique"] <= 1
    assert res["p_empirique"] >= 1 / (1 + n["n_essais"]) - 1e-9
    attendu = (1 + n["au_moins_aussi_bien"]) / (1 + n["n_essais"])
    assert abs(res["p_empirique"] - attendu) < 1e-6


def test_plus_de_temoins_rend_le_verdict_plus_prudent():
    """Le protocole ne doit pas devenir plus affirmatif quand on l'éprouve
    davantage : sur du bruit, aucun nombre de témoins ne doit produire un
    verdict de signal."""
    rng = random.Random(25)
    T = tirages_synthetiques(450, rng)
    for n_nuls in (3, 6):
        res = R.etude_complete(LOTO, T, random.Random(26), budget=40,
                               n_nuls=n_nuls, departs=1)
        assert "Aucun signal" in res["verdict"] or "limite" in res["verdict"]


def test_etude_refuse_un_historique_trop_court():
    rng = random.Random(17)
    assert R.etude_complete(LOTO, tirages_synthetiques(150, rng),
                            random.Random(18), budget=10, n_nuls=1, departs=1) is None


def test_recherche_est_deterministe_a_graine_egale():
    rng = random.Random(19)
    T = tirages_synthetiques(300, rng)
    X, cibles = R.construire(LOTO, T)
    a = R.chercher(X, cibles, LOTO["pick"], random.Random(20), budget=40, departs=2)
    b = R.chercher(X, cibles, LOTO["pick"], random.Random(20), budget=40, departs=2)
    assert a["poids"] == b["poids"]
    assert a["score_validation"] == b["score_validation"]


def test_le_verdict_ne_promet_jamais_rien():
    """Garde-fou n°2 : aucune promesse prédictive, nulle part."""
    rng = random.Random(21)
    res = R.etude_complete(LOTO, tirages_synthetiques(400, rng),
                           random.Random(22), budget=40, n_nuls=2, departs=1)
    interdits = ("garanti", "gagnant assuré", "va sortir", "prédit",
                 "infaillible", "certain de gagner")
    verdict = res["verdict"].lower()
    for mot in interdits:
        assert mot not in verdict
