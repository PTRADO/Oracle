"""Tests écrits en réponse à des MUTATIONS SURVIVANTES.

Chaque test de ce fichier existe parce qu'un bug injecté délibérément dans le
moteur a laissé la suite verte. Le protocole est celui de `tools/mutation.py` :
on injecte, on constate que rien ne rougit, on écrit le test, on réinjecte pour
vérifier qu'il rougit désormais.

Ne pas les affaiblir sans réinjecter la mutation correspondante : c'est
exactement l'erreur qui avait verrouillé l'inversion des rangs 6/7.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import pytest
from test_rangs import tirages_des_archives

import recherche
from oracle import (
    JEUX,
    calibration_empirique,
    grille_valide,
    retro_simulation,
)

IDS = ["loto", "euromillions"]
TOUS = [JEUX["loto"], JEUX["euromillions"]]


# ===========================================================================
# Mutation « grille_valide_sans_contrainte_de_parite »
# Mutation « grille_valide_sans_contrainte_de_dizaines »
# ===========================================================================
#
# Pourquoi elles ont survécu : le seul test qui regardait les contraintes de
# forme appelait `grille_valide` pour VÉRIFIER `grille_valide`. Retirer une
# contrainte rendait le test d'accord avec le code mutilé — il ne testait rien.
# Un test de forme doit énoncer la règle, pas la relire.

@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_grille_valide_refuse_une_parite_hors_bornes(cfg):
    """La règle, énoncée ici et pas relue depuis le code : entre 2 et 3
    numéros pairs sur 5. Une grille tout impaire ou tout paire est rejetée."""
    cts = {"somme_min": 0, "somme_max": 10_000}
    assert not grille_valide(cfg, (1, 3, 5, 27, 49), cts), "5 impairs accepté"
    assert not grille_valide(cfg, (2, 4, 6, 28, 48), cts), "5 pairs accepté"
    assert not grille_valide(cfg, (1, 3, 5, 7, 28), cts), "1 pair accepté"
    assert not grille_valide(cfg, (2, 4, 6, 8, 27), cts), "4 pairs accepté"
    # 2 et 3 pairs, eux, doivent passer (le reste des contraintes étant tenu)
    assert grille_valide(cfg, (2, 4, 15, 27, 39), cts)
    assert grille_valide(cfg, (2, 4, 16, 27, 39), cts)


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_grille_valide_exige_au_moins_deux_dizaines(cfg):
    """Au moins 2 dizaines distinctes — seuil abaissé de 3 à 2 en v2.4.

    Le seuil à 3 coûtait le plus cher des quatre contraintes de forme
    (15,2 % de partage au Loto, 23,0 % à l'EuroMillions) pour le moins de
    plausibilité gagnée : la meilleure grille qu'il interdisait est
    4-31-32-36-37, qui n'a rien d'étrange à l'œil.

    Une grille sur UNE seule dizaine reste refusée.

    ATTENTION au choix des cas — c'est le test de mutation qui l'a montré.
    21-23-25-27-29 et 2-4-6-8-10 tiennent bien sur une seule dizaine, mais ils
    sont TOUT impairs / TOUT pairs : c'est la contrainte de PARITÉ qui les
    rejette, pas celle des dizaines. Écrire le test avec ces cas-là le laissait
    vert alors même qu'on retirait la contrainte testée.

    Les cas ci-dessous isolent la contrainte : parité valide (2 pairs), pas de
    suite de 3, somme dans les bornes. Seules les dizaines peuvent les refuser.
    """
    cts = {"somme_min": 0, "somme_max": 10_000}
    assert not grille_valide(cfg, (21, 22, 24, 27, 29), cts), (
        "1 dizaine accepté (2 pairs, pas de suite de 3 : rien d'autre ne "
        "peut rejeter cette grille)")
    assert not grille_valide(cfg, (31, 32, 34, 37, 39), cts), "1 dizaine accepté"
    assert grille_valide(cfg, (4, 31, 32, 36, 37), cts), "2 dizaines refusé"
    assert grille_valide(cfg, (2, 4, 15, 27, 39), cts), "4 dizaines refusé"


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_grille_valide_refuse_trois_numeros_consecutifs(cfg):
    """Une suite de 3 est rejetée ; une paire consécutive reste permise —
    elle est même recherchée, `consecutifs` ayant un theta négatif."""
    cts = {"somme_min": 0, "somme_max": 10_000}
    assert not grille_valide(cfg, (11, 12, 13, 27, 40), cts)
    assert grille_valide(cfg, (11, 12, 25, 27, 40), cts)


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_grille_valide_respecte_les_bornes_de_somme(cfg):
    cts = {"somme_min": 100, "somme_max": 120}
    assert not grille_valide(cfg, (2, 4, 15, 27, 39), cts)   # somme 87
    cts = {"somme_min": 80, "somme_max": 90}
    assert grille_valide(cfg, (2, 4, 15, 27, 39), cts)       # somme 87


# ===========================================================================
# Mutation « backtest_fuite_du_futur »
# ===========================================================================
#
# Pourquoi elle a survécu : la grille jouée à chaque tirage n'était observable
# de nulle part. Une fuite d'un seul cran — `tirages[:i+1]` au lieu de
# `tirages[:i]` — laisse la rétro-simulation regarder le tirage sur lequel
# elle parie, ce qui gonfle le ROI affiché sans que rien ne plante.
#
# `retour_grilles=True` expose les grilles jouées, hors export.

@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_la_retro_simulation_ne_regarde_jamais_le_tirage_sur_lequel_elle_parie(
        cle):
    """Invariant : la grille jouée au tirage i ne dépend QUE des tirages
    antérieurs.

    On n'altère que le DERNIER tirage simulé. C'est le seul dont le résultat
    n'entre légitimement dans le passé d'aucun autre : modifier les tirages
    intermédiaires changerait le passé — réel — des suivants, et le test
    échouerait sur du code sain. (Première version de ce test : elle échouait
    pour cette raison exacte.)

    Toutes les grilles jouées doivent donc rester rigoureusement identiques,
    y compris la dernière. Seuls les gains changent, ce qui est normal.
    """
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    n = 12

    rng = random.Random(20260805)
    dernier = tir[-1]
    truque = list(tir[:-1]) + [{
        **dernier,
        "balls": tuple(sorted(rng.sample(range(1, cfg["n_max"] + 1),
                                         cfg["pick"]))),
        "bonus": tuple(sorted(rng.sample(range(1, cfg["bonus_max"] + 1),
                                         cfg["bonus_pick"])))}]
    assert truque[-1]["balls"] != dernier["balls"], "tirage non altéré"

    a = retro_simulation(cfg, tir, n, iters=1500, retour_grilles=True)
    b = retro_simulation(cfg, truque, n, iters=1500, retour_grilles=True)
    for mode in ("anti", "hybride", "pronostic"):
        ga = a["modes"][mode]["grilles_jouees"]
        gb = b["modes"][mode]["grilles_jouees"]
        assert ga and len(ga) == len(gb) == n
        assert ga == gb, (
            f"mode {mode} : les grilles jouées changent quand on modifie les "
            f"tirages sur lesquels on parie — fuite du futur")


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_la_retro_simulation_mise_le_prix_de_chaque_tirage_une_fois(cle):
    """Garde-fou attenant : une mise par tirage et par mode, au tarif de
    l'époque. Une fuite ou un décalage d'indice se verrait aussi ici."""
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    n = 12
    sim = retro_simulation(cfg, tir, n, iters=1500, retour_grilles=True)
    assert sim["n_tirages"] == n
    for mode in ("anti", "hybride", "pronostic"):
        v = sim["modes"][mode]
        dates = [g["date"] for g in v["grilles_jouees"]]
        assert dates == sorted(dates), "tirages rejoués dans le désordre"
        assert len(set(dates)) == n, "un tirage joué deux fois"
        assert dates == [t["date"].isoformat() for t in tir[-n:]]


# ===========================================================================
# Mutation « calibration_sans_effet_fixe_de_tirage »
# ===========================================================================
#
# Cas instructif : la mutation ne change PAS les coefficients. La matrice des
# régresseurs étant déjà centrée intra-tirage, Σ w·x·ȳ = 0 et retrancher ȳ de y
# est algébriquement redondant. Les gamma bougent de 7·10⁻¹¹.
#
# Elle détruit en revanche le R² partiel publié (0,89 → 0,0002), c'est-à-dire
# le chiffre sur lequel un lecteur juge si la calibration vaut quelque chose.
# C'est cela qui n'était pas couvert.

@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_r2_partiel_publie_mesure_bien_quelque_chose(cle):
    """Le R² partiel doit dire ce qu'apportent gamma, delta et theta AU-DELÀ
    des seuls effets de rang. S'il s'effondre vers 0 alors que les
    coefficients sont bons, c'est qu'il ne mesure plus ce qu'il annonce."""
    cfg = JEUX[cle]
    calib = calibration_empirique(cfg, tirages_des_archives(cle))
    assert calib is not None
    assert 0.3 < calib["r2"] < 1.0, (
        f"R² partiel = {calib['r2']} — hors de toute plage crédible pour une "
        f"calibration dont {calib['n_significatifs']}/{cfg['n_max']} "
        f"coefficients sortent du bruit")


# ===========================================================================
# Mutation « recherche_deux_traits_permutes »
# ===========================================================================
#
# Pourquoi elle a survécu : `TRAITS` n'est qu'une liste de LIBELLÉS, l'ordre
# réel étant fixé par `_Etat.traits()`. Les permuter ne change aucun score —
# seulement le NOM sous lequel chaque poids est publié dans l'export. Un
# lecteur conclurait « la formule s'appuie sur les fréquences longues » quand
# elle s'appuie sur les courtes.

def _histoire_synthetique(cfg, n_tirages: int, plan) -> list[dict]:
    """`plan(i)` rend les 5 boules du tirage i."""
    d0 = date(2020, 1, 4)
    out = []
    for i in range(n_tirages):
        jour = d0 + timedelta(days=i)
        out.append({"date": jour, "jour": jour.weekday(),
                    "balls": tuple(sorted(plan(i))), "bonus": (1,),
                    "gagnants": {}, "rapports": {}})
    return out


def test_les_libelles_de_traits_designent_bien_les_traits_calcules():
    """Ancre les libellés sur des scénarios où le classement est connu
    d'avance, plutôt que sur l'ordre du code.

    Construction : les numéros 41-45 ne sortent QUE dans les 40 derniers
    tirages, les numéros 11-15 QUE bien avant. Alors :
      · `freq_50`  (fenêtre courte) doit classer 41-45 au-dessus de 11-15 ;
      · `freq_250` (fenêtre longue) doit classer 11-15 au-dessus de 41-45 ;
      · `retard` doit classer 11-15 au-dessus de 41-45 (sortis plus anciennement).
    Permuter deux de ces libellés inverse au moins une de ces trois relations.
    """
    cfg = JEUX["loto"]
    n = 300

    def plan(i):
        return (11, 12, 13, 14, 15) if i < n - 40 else (41, 42, 43, 44, 45)

    tir = _histoire_synthetique(cfg, n, plan)
    etat = recherche._Etat(cfg["n_max"], cfg["pick"])
    for t in tir:
        etat.absorber(t)
    traits = etat.traits(tir[-1]["jour"])
    assert len(traits) == recherche.N_TRAITS == len(recherche.TRAITS)

    def valeur(nom, numero):
        return traits[recherche.TRAITS.index(nom)][numero - 1]

    recents, anciens = 43, 13
    assert valeur("freq_50", recents) > valeur("freq_50", anciens), (
        "« freq_50 » ne désigne pas la fenêtre courte")
    assert valeur("freq_250", anciens) > valeur("freq_250", recents), (
        "« freq_250 » ne désigne pas la fenêtre longue")
    assert valeur("freq_tout", anciens) > valeur("freq_tout", recents), (
        "« freq_tout » ne désigne pas la fréquence sur tout l'historique")
    assert valeur("retard", anciens) > valeur("retard", recents), (
        "« retard » ne compte pas les tirages depuis la dernière sortie")
    assert valeur("ewma_10", recents) > valeur("ewma_10", anciens), (
        "« ewma_10 » ne pondère pas le récent")


def test_le_libelle_voisins_designe_bien_les_numeros_adjacents():
    """`voisins` doit valoir plus pour un numéro dont n−1 ou n+1 vient de
    sortir que pour un numéro isolé."""
    cfg = JEUX["loto"]

    def plan(i):
        return (10, 20, 30, 40, 49)

    tir = _histoire_synthetique(cfg, 120, plan)
    etat = recherche._Etat(cfg["n_max"], cfg["pick"])
    for t in tir:
        etat.absorber(t)
    traits = etat.traits(tir[-1]["jour"])
    v = traits[recherche.TRAITS.index("voisins")]
    assert v[21 - 1] > v[25 - 1], "« voisins » ne regarde pas n−1 / n+1"
    assert v[19 - 1] > v[25 - 1]


def test_les_libelles_de_traits_sont_uniques_et_en_nombre_juste():
    assert len(set(recherche.TRAITS)) == len(recherche.TRAITS)
    assert len(recherche.TRAITS) == recherche.N_TRAITS


# ===========================================================================
# Mutant ÉQUIVALENT, documenté pour qu'on ne le rechasse pas
# ===========================================================================

def test_le_seuil_du_rang_chance_seul_est_inatteignable_au_dela_de_1():
    """`rang_gagne` teste `b == 1 and m <= 1` APRÈS la table, qui couvre déjà
    (2,1) → rang 7. Écrire `m <= 2` ne change donc rien : la branche est
    inatteignable pour m = 2.

    La mutation correspondante survit à la suite, et c'est normal — aucun test
    ne peut distinguer deux programmes qui calculent la même fonction. Ce test
    fige le raisonnement pour qu'un futur auditeur ne reparte pas en chasse.
    """
    from oracle import rang_gagne
    loto = JEUX["loto"]
    couples_dans_la_table = {(5, 1), (5, 0), (4, 1), (4, 0), (3, 1), (3, 0),
                             (2, 1), (2, 0)}
    restants = [(m, b) for m in range(loto["pick"] + 1) for b in range(2)
                if (m, b) not in couples_dans_la_table]
    assert restants == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert max(m for m, b in restants if b == 1) == 1
    assert rang_gagne(loto, 2, 1) == 7
    assert rang_gagne(loto, 1, 1) == rang_gagne(loto, 0, 1) == 9


# ===========================================================================
# Mutation « backtest_absorbe_avant_de_predire »
# ===========================================================================
#
# La plus instructive des survivantes, et la plus gênante.
#
# Le backtest walk-forward est le garde-fou n°1 du produit : il prouve à
# chaque mise à jour que le folklore ne prédit rien. Or son résultat attendu
# — « le modèle fait comme le hasard » — est exactement ce que produit un
# backtest CASSÉ. Un instrument en panne et un instrument honnête rendent le
# même chiffre.
#
# Deux raisons à la survie :
#   · `test_le_folklore_ne_bat_pas_le_hasard` lit l'export COMMITÉ, pas un
#     calcul frais : muter le code ne change pas le fichier ;
#   · même recalculé, « pas de signal » ne distingue pas les deux cas.
#
# On étalonne donc l'instrument sur une entrée où la bonne réponse est connue,
# comme le fait déjà `test_chercheur_retrouve_un_signal_plante`.

_PLANTES = (45, 46, 47)


def _histoire_avec_signal(cfg, n_tirages: int, graine: int) -> list[dict]:
    """Trois numéros sortent à TOUS les tirages. Un backtest qui fonctionne
    doit les trouver ; un backtest gelé ne le peut pas.

    Les numéros plantés sont volontairement HAUTS : avec un état gelé,
    `normaliser` rend 50 partout, le tri devient stable et rend [1, 2, 3, …].
    Planter sur 1-2-3 aurait donc été trouvé PAR ACCIDENT par un backtest en
    panne — première version de ce test, qui ne discriminait rien.
    """
    rng = random.Random(graine)
    d0 = date(2020, 1, 4)
    out = []
    libres = [x for x in range(1, cfg["n_max"] + 1) if x not in _PLANTES]
    for i in range(n_tirages):
        jour = d0 + timedelta(days=i)
        autres = rng.sample(libres, cfg["pick"] - len(_PLANTES))
        out.append({"date": jour, "jour": jour.weekday(),
                    "balls": tuple(sorted((*_PLANTES, *autres))),
                    "bonus": (1,), "gagnants": {}, "rapports": {}})
    return out


def test_le_backtest_retrouve_un_signal_planté():
    """Étalonnage de l'instrument. Mesuré : 3,006 bons numéros sur un
    historique où trois numéros sortent toujours, contre 0,238 quand le
    backtest n'apprend plus rien. La marge est franche."""
    from oracle import backtest
    cfg = JEUX["loto"]
    tir = _histoire_avec_signal(cfg, 400, graine=7)
    r = backtest(cfg, tir, random.Random(0))
    assert r["n_tests"] > 300
    assert r["modele"] > 2.5, (
        f"le backtest ne retrouve pas trois numéros qui sortent à TOUS les "
        f"tirages ({r['modele']:.3f} bon(s) en moyenne) — l'instrument est "
        f"en panne, et sa panne est indiscernable de son verdict habituel")
    # le bras « froid » doit faire l'inverse : éviter les numéros plantés
    assert r["froid"] < 1.0, (
        f"le bras froid trouve {r['froid']:.3f} numéros plantés — les deux "
        f"bras du backtest sont probablement intervertis")
    assert r["modele"] > r["aleatoire"] > r["froid"]


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_backtest_recalcule_ne_bat_pas_le_hasard_sur_les_vraies_donnees(cle):
    """Le garde-fou, recalculé ici et non relu depuis l'export.

    L'export peut dater ; le code, lui, est celui qu'on exécute. Les deux
    doivent dire la même chose, sans quoi la page affiche un verdict qui n'est
    plus celui du moteur.
    """
    from oracle import backtest
    cfg = JEUX[cle]
    r = backtest(cfg, tirages_des_archives(cle), random.Random(0))
    seuil = 2 * (0.45 / r["n_tests"]) ** 0.5
    ecart = abs(r["modele"] - r["theorique"])
    assert ecart <= 1.5 * seuil, (
        f"écart {ecart:+.4f} hors bruit (seuil {seuil:.4f}) — avant de crier "
        f"à la découverte, suspecter l'intégrité des données ou une fuite du "
        f"futur dans le walk-forward")
    assert r["n_tests"] > 100


# ===========================================================================
# v2.5 — la dispersion de « si tu avais joué »
# ===========================================================================

@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_la_dispersion_mesure_bien_l_effet_de_la_seule_graine(cle):
    """Les graines alternatives doivent différer entre elles — sinon le champ
    affiché ne mesure rien et donne une fausse impression de rigueur.

    Elles doivent aussi rester du même ordre de grandeur que le résultat
    publié : c'est la même stratégie, seule la graine change.
    """
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    s = retro_simulation(cfg, tir, 25, iters=2000,
                         n_graines_dispersion=10, iters_dispersion=1200)
    d = s["dispersion_anti"]
    assert d is not None and d["n_graines"] == 10
    assert d["min"] <= d["p10"] <= d["mediane"] <= d["p90"] <= d["max"]
    assert d["max"] > d["min"], (
        "toutes les graines rendent le même total — la dispersion mesurée "
        "est nulle, donc le champ ne mesure rien")


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_pas_de_dispersion_demandee_pas_de_champ(cle):
    """Le calcul est optionnel : il coûte du temps et n'a pas à s'imposer."""
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    s = retro_simulation(cfg, tir, 8, iters=1200)
    assert s["dispersion_anti"] is None
