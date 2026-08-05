"""Tests de PROPRIÉTÉ, MÉTAMORPHIQUES et DIFFÉRENTIEL.

Trois familles que la suite d'origine n'employait pas, et qui attrapent des
choses qu'un test par l'exemple ne voit jamais.

  · PROPRIÉTÉ — un énoncé qui doit tenir sur des milliers d'entrées tirées au
    hasard, plutôt que sur trois cas choisis par celui qui a écrit le code.
  · MÉTAMORPHIQUE — on transforme l'entrée d'une façon dont on connaît l'effet
    attendu sur la sortie. Pas besoin de connaître la bonne réponse : il
    suffit de connaître la bonne DIFFÉRENCE. C'est ce qui permet de tester le
    TRJ ou les scores, dont personne ne connaît la valeur juste a priori.
  · DIFFÉRENTIEL — une seconde implémentation, écrite depuis le règlement FDJ
    et non depuis le code, comparée sur 100 000 cas.
"""
from __future__ import annotations

import itertools
import math
import random
import statistics
from datetime import timedelta

import pytest
from test_rangs import tirages_des_archives

from oracle import (
    JEUX,
    bonus_nums,
    calibration_empirique,
    contraintes_historiques,
    decomposition_trj,
    generer_grilles,
    grille_valide,
    normaliser,
    nums,
    pop_rel_grille,
    rang_gagne,
    regler_grille,
    scores_anti,
    systeme_reducteur,
    t1_frequence,
    t2_retard,
)

IDS = ["loto", "euromillions"]
TOUS = [JEUX["loto"], JEUX["euromillions"]]


def grille_au_hasard(cfg, rng):
    return {"numeros": sorted(rng.sample(range(1, cfg["n_max"] + 1),
                                         cfg["pick"])),
            "bonus": sorted(rng.sample(range(1, cfg["bonus_max"] + 1),
                                       cfg["bonus_pick"]))}


def tirage_au_hasard(cfg, rng):
    return {"balls": tuple(sorted(rng.sample(range(1, cfg["n_max"] + 1),
                                             cfg["pick"]))),
            "bonus": tuple(sorted(rng.sample(range(1, cfg["bonus_max"] + 1),
                                             cfg["bonus_pick"]))),
            "rapports": {r: float(1000 - 10 * r) for r in range(1, 14)}}


# ===========================================================================
# 1. DIFFÉRENTIEL — seconde implémentation de la table des rangs
# ===========================================================================
#
# Écrite depuis le règlement FDJ, pas depuis `rang_gagne`, et sous une forme
# DIFFÉRENTE : une liste ordonnée du gain le plus élevé au plus faible, dont
# la position donne le rang. Une transposition dans le dictionnaire du moteur
# ne peut pas se reproduire à l'identique ici.

# LOTO — 9 rangs. Le dernier, « n° Chance seul », regroupe 0 et 1 bon numéro.
_LOTO_ORDRE = [(5, 1), (5, 0), (4, 1), (4, 0), (3, 1), (3, 0), (2, 1), (2, 0)]

# EUROMILLIONS — 13 rangs, classés par MONTANT décroissant. 3+2 passe devant
# 4+0 bien qu'il soit un peu plus rare : c'est le gain qui ordonne, pas la
# probabilité. Ancré hors du moteur par les rapports FDJ réels
# (cf. test_rangs.test_ordre_des_rangs_conforme_aux_rapports_fdj).
_EURO_ORDRE = [(5, 2), (5, 1), (5, 0), (4, 2), (4, 1), (3, 2), (4, 0),
               (2, 2), (3, 1), (3, 0), (1, 2), (2, 1), (2, 0)]


def rang_temoin(cfg, m: int, b: int) -> int | None:
    """Implémentation témoin, indépendante de `rang_gagne`."""
    if cfg["bonus_pick"] == 1:
        for i, couple in enumerate(_LOTO_ORDRE, start=1):
            if (m, b) == couple:
                return i
        return 9 if (b == 1 and m <= 1) else None
    for i, couple in enumerate(_EURO_ORDRE, start=1):
        if (m, b) == couple:
            return i
    return None


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_differentiel_table_des_rangs_exhaustif(cfg):
    """Tous les (m, b) possibles, sans exception."""
    for m in range(cfg["pick"] + 1):
        for b in range(cfg["bonus_pick"] + 1):
            assert rang_gagne(cfg, m, b) == rang_temoin(cfg, m, b), (
                f"{cfg['nom']} : {m} bons + {b} bonus → moteur "
                f"{rang_gagne(cfg, m, b)}, témoin {rang_temoin(cfg, m, b)}")


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_differentiel_reglement_sur_100k_cas(cfg):
    """Règlement complet d'une grille contre un tirage, 100 000 fois.

    Le témoin recalcule l'intersection et le rang par un chemin distinct ;
    seuls les rapports viennent du tirage, comme il se doit.
    """
    rng = random.Random(20260805)
    for _ in range(100_000):
        g = grille_au_hasard(cfg, rng)
        t = tirage_au_hasard(cfg, rng)
        r = regler_grille(cfg, g, t)

        m = len(set(g["numeros"]) & set(t["balls"]))
        b = len(set(g["bonus"]) & set(t["bonus"]))
        rang = rang_temoin(cfg, m, b)
        gain = t["rapports"].get(rang, 0.0) if rang is not None else 0.0

        assert r["rang"] == rang
        assert r["gain"] == pytest.approx(gain)
        assert r["bons"] == sorted(set(g["numeros"]) & set(t["balls"]))
        assert r["bonus_ok"] == sorted(set(g["bonus"]) & set(t["bonus"]))


# ===========================================================================
# 2. PROPRIÉTÉS
# ===========================================================================

@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_propriete_reglement_toujours_coherent(cfg):
    rng = random.Random(7)
    for _ in range(20_000):
        g = grille_au_hasard(cfg, rng)
        t = tirage_au_hasard(cfg, rng)
        r = regler_grille(cfg, g, t)
        assert r["gain"] >= 0.0
        assert set(r["bons"]) <= set(g["numeros"]) & set(t["balls"])
        assert set(r["bons"]) >= set(g["numeros"]) & set(t["balls"])
        assert r["bons"] == sorted(r["bons"]), "bons non triés"
        assert len(r["bons"]) <= cfg["pick"]
        if r["rang"] is None:
            assert r["gain"] == 0.0
        else:
            assert 1 <= r["rang"] <= (9 if cfg["bonus_pick"] == 1 else 13)


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_propriete_normaliser_borne_et_preserve_l_ordre(cfg):
    rng = random.Random(3)
    for _ in range(500):
        s = {n: rng.gauss(0, 10) for n in nums(cfg)}
        out = normaliser(s)
        assert set(out) == set(s)
        assert all(0.0 <= v <= 100.0 for v in out.values())
        assert min(out.values()) == pytest.approx(0.0)
        assert max(out.values()) == pytest.approx(100.0)
        # monotonie stricte préservée
        ordre_avant = sorted(s, key=lambda n: s[n])
        ordre_apres = sorted(out, key=lambda n: out[n])
        assert ordre_avant == ordre_apres


def test_propriete_normaliser_survit_au_cas_degenere():
    """Toutes les valeurs égales : pas de division par zéro, sortie neutre."""
    out = normaliser({1: 4.0, 2: 4.0, 3: 4.0})
    assert set(out.values()) == {50.0}


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_propriete_le_systeme_reducteur_tient_sa_garantie(cfg):
    """La promesse « ≥ 3 bons garantis si les 5 sortent du pool » est
    re-vérifiée ici sur TOUS les 5-uplets, hors de la fonction qui la
    produit."""
    pool = sorted(random.Random(1).sample(range(1, cfg["n_max"] + 1), 8))
    grilles = systeme_reducteur(cfg, pool, garantie=3)
    assert grilles
    for u in itertools.combinations(pool, cfg["pick"]):
        assert any(len(set(g) & set(u)) >= 3 for g in grilles), (
            f"5-uplet {u} non couvert par {grilles}")
    for g in grilles:
        assert len(set(g)) == cfg["pick"]
        assert set(g) <= set(pool)


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_propriete_toute_grille_generee_passe_grille_valide(cle):
    """Le générateur ne doit jamais publier une grille que ses propres
    contraintes rejettent."""
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    calib = calibration_empirique(cfg, tir)
    cts = contraintes_historiques(tir)
    anti, _ = scores_anti(cfg, calib)
    from oracle import bonus_scores
    sb = bonus_scores(cfg, tir, calib)
    for mode in ("anti", "hybride", "pronostic"):
        grilles = generer_grilles(cfg, anti, sb, tir, mode, 5,
                                  random.Random(0), calib, iters=4000)
        assert grilles
        for g in grilles:
            assert grille_valide(cfg, tuple(g["numeros"]), cts)
            assert len(set(g["numeros"])) == cfg["pick"]
            assert len(set(g["bonus"])) == cfg["bonus_pick"]
            assert all(1 <= b <= cfg["bonus_max"] for b in g["bonus"])
            assert g["pop_rel"] > 0


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_propriete_le_detail_du_trj_recompose_le_total(cle):
    """Le TRJ hors jackpot plus la part du jackpot doit redonner le total."""
    d = decomposition_trj(JEUX[cle], tirages_des_archives(cle))
    assert 0.0 <= d["part_jackpot"] <= 1.0
    assert d["trj_total"] >= d["trj_hors_jackpot"] - 1e-9
    # `decomposition_trj` arrondit ses sorties à 4 décimales : la relation
    # algébrique ne peut donc tenir qu'à 3 arrondis près, soit 1,5·10⁻⁴.
    # Tolérance déduite de l'arrondi, pas choisie pour faire passer.
    assert d["trj_hors_jackpot"] == pytest.approx(
        d["trj_total"] * (1 - d["part_jackpot"]), abs=1.5e-4)
    assert d["n_tirages"] > 0


# ===========================================================================
# 3. MÉTAMORPHIQUES
# ===========================================================================

@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_metamorphique_renommer_les_numeros_ne_change_pas_le_reglement(cfg):
    """On applique la même permutation aux numéros de la grille ET du tirage.
    Le nombre de bons numéros — donc le rang, donc le gain — est invariant.

    Attrape toute logique qui dépendrait de la VALEUR d'un numéro là où elle
    ne devrait dépendre que de sa coïncidence.
    """
    rng = random.Random(42)
    perm = list(range(1, cfg["n_max"] + 1))
    rng.shuffle(perm)
    sigma = {i + 1: perm[i] for i in range(cfg["n_max"])}
    for _ in range(5_000):
        g = grille_au_hasard(cfg, rng)
        t = tirage_au_hasard(cfg, rng)
        avant = regler_grille(cfg, g, t)
        g2 = {**g, "numeros": sorted(sigma[n] for n in g["numeros"])}
        t2 = {**t, "balls": tuple(sorted(sigma[n] for n in t["balls"]))}
        apres = regler_grille(cfg, g2, t2)
        assert apres["rang"] == avant["rang"]
        assert apres["gain"] == pytest.approx(avant["gain"])
        assert len(apres["bons"]) == len(avant["bons"])
        assert apres["bons"] == sorted(sigma[n] for n in avant["bons"])


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_metamorphique_dupliquer_l_historique_ne_change_pas_les_scores(cle):
    """Concaténer l'historique à lui-même double les fréquences brutes mais
    ne change RIEN aux scores normalisés : la forme de la distribution est
    identique. Un score qui bougerait dépendrait de la TAILLE de
    l'échantillon là où il ne devrait dépendre que de sa forme.
    """
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    f1 = t1_frequence(cfg, tir)
    f2 = t1_frequence(cfg, tir + tir)
    for n in nums(cfg):
        assert f2[n] == pytest.approx(2 * f1[n])
    n1, n2 = normaliser(f1), normaliser(f2)
    for n in nums(cfg):
        assert n2[n] == pytest.approx(n1[n], abs=1e-9)


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_metamorphique_decaler_toutes_les_dates_ne_change_pas_les_scores(cle):
    """Un décalage d'un an conserve l'ordre et les écarts : tout ce qui
    dépend de la CHRONOLOGIE doit être invariant. Le prix, lui, dépend
    légitimement de la date : il n'est pas testé ici.
    """
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    decale = [{**t, "date": t["date"] + timedelta(days=364)} for t in tir]
    assert t1_frequence(cfg, tir) == t1_frequence(cfg, decale)
    assert t2_retard(cfg, tir) == t2_retard(cfg, decale)


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_metamorphique_doubler_les_rapports_double_le_trj(cle):
    """Le TRJ est un rapport gains/mises : doubler tous les rapports FDJ doit
    exactement le doubler. Les mises, elles, ne bougent pas — elles se
    déduisent du nombre de gagnants, pas des montants.

    C'est le test qui verrouille le sens de la division. Une inversion
    numérateur/dénominateur passerait inaperçue sur une valeur seule.
    """
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    double = [{**t, "rapports": {r: 2 * v for r, v in t["rapports"].items()}}
              for t in tir]
    a = decomposition_trj(cfg, tir)
    b = decomposition_trj(cfg, double)
    # Même remarque : on compare 2×round(x) à round(2x), d'où |écart| ≤ 3
    # demi-ulps de l'arrondi à 4 décimales = 1,5·10⁻⁴.
    assert b["trj_total"] == pytest.approx(2 * a["trj_total"], abs=1.5e-4)
    assert b["trj_hors_jackpot"] == pytest.approx(
        2 * a["trj_hors_jackpot"], abs=1.5e-4)
    # le SENS de la division, lui, doit être exact : doubler les rapports
    # double les gains et laisse les mises inchangées.
    assert b["trj_total"] > 1.9 * a["trj_total"]


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_metamorphique_permuter_deux_numeros_permute_leur_popularite(cle):
    """Si l'on échange systématiquement deux numéros dans TOUT l'historique,
    la calibration doit échanger leurs gamma — et ne toucher à rien d'autre.

    C'est le test métamorphique du moteur anti-partage : il vérifie que gamma
    dépend bien des données et non d'une propriété arithmétique du numéro.
    """
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    # SANS les co-occurrences : leurs prédicats portent sur la VALEUR des
    # numéros (≤ 31, ≤ 12, consécutifs, même dizaine). Échanger 13 et 36
    # change donc réellement la structure de co-occurrence du tirage — ce
    # n'est pas un pur rebaptême, et gamma n'a aucune raison de se contenter
    # de permuter. L'invariant testé ici ne concerne que l'estimateur
    # marginal, où il doit tenir exactement.
    base = calibration_empirique(cfg, tir, avec_paires=False)
    x, y = base["top_surjoues"][0], base["top_delaisses"][0]

    def echange(n):
        return y if n == x else (x if n == y else n)

    permute = [{**t, "balls": tuple(sorted(echange(n) for n in t["balls"]))}
               for t in tir]
    autre = calibration_empirique(cfg, permute, avec_paires=False)
    assert autre["gamma"][y] == pytest.approx(base["gamma"][x], abs=0.02)
    assert autre["gamma"][x] == pytest.approx(base["gamma"][y], abs=0.02)
    intacts = [n for n in nums(cfg) if n not in (x, y)]
    ecart = max(abs(autre["gamma"][n] - base["gamma"][n]) for n in intacts)
    assert ecart < 0.05, f"les autres numéros ont bougé de {ecart:.3f}"


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_metamorphique_pop_rel_est_invariant_par_ordre_des_numeros(cle):
    cfg = JEUX[cle]
    calib = calibration_empirique(cfg, tirages_des_archives(cle))
    rng = random.Random(5)
    for _ in range(500):
        b = rng.sample(range(1, cfg["n_max"] + 1), cfg["pick"])
        a = pop_rel_grille(cfg, tuple(b), calib)
        rng.shuffle(b)
        assert pop_rel_grille(cfg, tuple(b), calib) == pytest.approx(a)


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_metamorphique_ajouter_des_gagnants_partout_ne_change_pas_gamma(cle):
    """Multiplier le nombre de gagnants de TOUS les rangs d'un tirage par un
    même facteur simule une hausse de participation. gamma ne doit pas
    bouger : c'est exactement ce que l'effet fixe de tirage doit absorber.

    C'est LE test de la v2.4. Avec la spécification v2.3 (log du total des
    gagnants, tendance t/t²), un tel choc n'était PAS absorbé.
    """
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    base = calibration_empirique(cfg, tir)
    rng = random.Random(2026)
    choque = []
    for t in tir:
        f = math.exp(rng.gauss(0, 0.8))       # ±120 % de participation
        choque.append({**t, "gagnants": {r: max(1, int(w * f))
                                         for r, w in t["gagnants"].items()}})
    autre = calibration_empirique(cfg, choque)
    ecart = max(abs(autre["gamma"][n] - base["gamma"][n]) for n in nums(cfg))
    assert ecart < 0.02, (
        f"un choc de participation déplace gamma de {ecart:.4f} — l'effet "
        f"fixe de tirage n'absorbe pas ce qu'il devrait")
    ns = list(nums(cfg))
    r = statistics.correlation([base["gamma"][n] for n in ns],
                               [autre["gamma"][n] for n in ns])
    assert r > 0.999
