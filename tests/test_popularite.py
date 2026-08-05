"""Le moteur anti-partage — le seul levier que ce produit revendique.

Jusqu'en v2.3 il n'avait AUCUN test. C'est la zone la plus exposée du moteur :
une erreur ici ne plante rien, elle change silencieusement les numéros publiés.

Ce fichier valide la calibration de quatre façons indépendantes :

  1. PLACEBO — on remplace les boules par un tirage indépendant. L'estimateur
     doit alors ne rien trouver, au taux d'erreur de première espèce près.
     C'est le test qui empêche de prendre du bruit pour un signal.
  2. FORME FONCTIONNELLE — le facteur m/pick est dérivé, pas choisi. On le
     vérifie en estimant gamma séparément sur les rangs à faible m et à fort
     m : la même échelle doit ressortir.
  3. STABILITÉ — gamma appris sur la 1re moitié de l'historique doit prédire
     la 2e.
  4. INVARIANTS — bornes, centrage, cohérence des signes.
"""
from __future__ import annotations

import math
import random
import statistics

import pytest
from conftest import RACINE  # noqa: F401
from test_rangs import tirages_des_archives

from oracle import (
    JEUX,
    MIN_TIRAGES_CALIBRATION,
    PAIRES_POPULARITE,
    bonus_nums,
    calibration_empirique,
    compter_paires,
    nums,
    pop_rel_grille,
    popularite_log,
    rangs_denses,
    rangs_mb,
    scores_anti,
    scores_anti_bonus,
    traits_paires,
)

IDS = ["loto", "euromillions"]
TOUS = [JEUX["loto"], JEUX["euromillions"]]


@pytest.fixture(scope="module")
def jeux():
    """(cfg, tirages, calibration) pour les deux jeux — calculé une fois."""
    out = {}
    for cle in IDS:
        cfg = JEUX[cle]
        tir = tirages_des_archives(cle)
        out[cle] = (cfg, tir, calibration_empirique(cfg, tir))
    return out


# ---------------------------------------------------------------------------
# 1. (m, b) effectifs — le dénombrement qui fixe l'échelle de tous les gamma
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_m_effectif_egale_m_pour_les_rangs_a_couple_unique(cfg):
    """Presque tous les rangs correspondent à un seul (m, b) : leur m effectif
    doit être cet entier exactement, sans quoi l'échelle des gamma dérive."""
    mb = rangs_mb(cfg)
    from oracle import rang_gagne
    for m in range(cfg["pick"] + 1):
        for b in range(cfg["bonus_pick"] + 1):
            r = rang_gagne(cfg, m, b)
            if r is None:
                continue
            couples = [(mm, bb) for mm in range(cfg["pick"] + 1)
                       for bb in range(cfg["bonus_pick"] + 1)
                       if rang_gagne(cfg, mm, bb) == r]
            if len(couples) == 1:
                assert mb[r] == pytest.approx((float(m), float(b)))


def test_loto_rang_9_a_un_m_effectif_fractionnaire():
    """Le rang « n° Chance seul » agrège m ∈ {0, 1}. Son m effectif est
    E[m | rang 9] = P(1)/(P(0)+P(1)), calculé à la main hors du moteur :

        P(0) = C(44,5)/C(49,5) = 1 086 008 / 1 906 884
        P(1) = 5·C(44,4)/C(49,5) =  678 755 / 1 906 884
        E[m | rang 9] = 678 755 / 1 764 763 = 0,384613…

    Le prendre pour 0 (ou pour 1) décalerait l'échelle de tous les gamma,
    puisque c'est le rang le plus peuplé de l'historique.
    """
    attendu = 678_755 / 1_764_763
    m, b = rangs_mb(JEUX["loto"])[9]
    assert m == pytest.approx(attendu, rel=1e-12)
    assert b == pytest.approx(1.0)


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_les_rangs_retenus_sont_denses_donc_non_selectionnes(cfg, jeux):
    """Un rang disponible seulement quand il est bien garni sélectionnerait
    les tirages populaires — exactement la variable qu'on mesure."""
    cle = "loto" if cfg["bonus_pick"] == 1 else "euromillions"
    _, tir, _ = jeux[cle]
    for r in rangs_denses(cfg, tir):
        manquants = sum(1 for t in tir if (t["gagnants"].get(r) or 0) < 30)
        assert manquants <= 0.01 * len(tir), (
            f"rang {r} absent de {manquants}/{len(tir)} tirages")


# ---------------------------------------------------------------------------
# 2. PLACEBO — l'estimateur ne doit rien trouver là où il n'y a rien
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_placebo_aucun_signal_sur_des_boules_independantes(cle, jeux):
    """On garde les gagnants réels et on remplace les boules par un tirage
    aléatoire indépendant. Le lien numéro↔gagnants est détruit ; si
    l'estimateur trouve encore « des numéros sur-joués », il fabrique du
    signal et tout le reste est à jeter.

    Attendu sous l'hypothèse nulle : ~5 % des |t| au-delà de 1,96. On tolère
    jusqu'à 15 % pour ne pas rendre le test instable, ce qui reste très loin
    des 96 % observés sur les vraies données.
    """
    cfg, tir, _ = jeux[cle]
    rng = random.Random(20260805)
    faux = []
    for t in tir:
        c = dict(t)
        c["balls"] = tuple(sorted(rng.sample(range(1, cfg["n_max"] + 1),
                                             cfg["pick"])))
        faux.append(c)
    calib = calibration_empirique(cfg, faux)
    assert calib is not None
    ts = [abs(calib["gamma"][n]) / calib["se_gamma"][n] for n in nums(cfg)]
    part = sum(1 for v in ts if v > 1.96) / len(ts)
    assert part <= 0.15, (
        f"{part:.0%} des numéros « significatifs » sur des boules aléatoires "
        f"— l'estimateur fabrique du signal")
    assert statistics.median(ts) < 1.5


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_signal_reel_est_tres_au_dessus_du_placebo(cle, jeux):
    """Le pendant du test précédent : sur les vraies données, l'écrasante
    majorité des numéros doit sortir du bruit. Sinon la calibration ne mérite
    pas de piloter les grilles publiées."""
    cfg, _, calib = jeux[cle]
    assert calib is not None
    assert calib["n_significatifs"] >= 0.7 * cfg["n_max"]
    assert calib["t_median"] > 3.0


# ---------------------------------------------------------------------------
# 3. FORME FONCTIONNELLE — le facteur m/pick est-il le bon ?
# ---------------------------------------------------------------------------

def test_le_facteur_m_sur_pick_donne_la_meme_echelle_a_tous_les_rangs():
    """Test central de la spécification, sur le Loto (le seul jeu dont les
    rangs offrent assez de variation en m des deux côtés).

    Si log W_m = cst + (m/pick)·Σ gamma, alors gamma estimé sur les rangs à m
    faible et sur ceux à m fort doit avoir la MÊME échelle. On compare des SD
    corrigées de la variance d'échantillonnage (une SD brute est gonflée par
    le bruit, et davantage du côté le moins peuplé).

    Le ridge de production est conservé : à lambda ~ 0 le groupe à m faible
    n'identifie plus rien (SD corrigée nulle, les rangs 7, 8 et 9 ont m = 2, 2
    et 0,38, donc trop peu de variation). Qu'on obtienne le même ratio à
    lambda = 0,5 et 2,0 montre que la contraction ridge n'est pas ce qui
    produit le résultat.

    Le test vérifie aussi que RETIRER les co-occurrences DÉGRADE cet accord
    (0,81 au lieu de 0,96). Les omettre biaise les marginaux, et d'autant plus
    que m est grand puisque leur poids croît en C(m,2). C'est la raison
    quantitative de les garder — pas une préférence de modélisation.
    """
    cfg = JEUX["loto"]
    tir = tirages_des_archives("loto")

    def echelle(rangs, lam, paires=True):
        c = calibration_empirique(cfg, tir, lam=lam, rangs_forces=rangs,
                                  avec_paires=paires)
        assert c is not None
        v = statistics.pvariance(list(c["gamma"].values()))
        bruit = statistics.mean(s * s for s in c["se_gamma"].values())
        return math.sqrt(max(v - bruit, 0.0))

    ratios = []
    for lam in (0.5, 2.0):
        bas, haut = echelle([7, 8, 9], lam), echelle([4, 5, 6], lam)
        assert bas > 0 and haut > 0, f"aucun signal identifié à lambda={lam}"
        ratios.append(haut / bas)
        assert haut / bas == pytest.approx(1.0, abs=0.15), (
            f"lambda={lam} : échelles incohérentes entre rangs (m faible "
            f"{bas:.4f}, m fort {haut:.4f}) — le facteur m/pick est faux")
    assert abs(ratios[0] - ratios[1]) < 0.05, (
        f"ratio sensible au ridge ({ratios}) — résultat non concluant")

    sans = echelle([4, 5, 6], 0.5, paires=False) / echelle([7, 8, 9], 0.5,
                                                           paires=False)
    assert abs(sans - 1.0) > abs(ratios[0] - 1.0), (
        f"retirer les co-occurrences n'aggrave plus l'accord entre rangs "
        f"({sans:.3f} contre {ratios[0]:.3f}) — leur justification tombe")


# ---------------------------------------------------------------------------
# 4. STABILITÉ — ce qui est appris sur le passé vaut encore ensuite
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_gamma_est_stable_entre_les_deux_moities_de_l_historique(cle, jeux):
    """Si la popularité mesurée était du bruit, les deux moitiés seraient
    décorrélées. Elles ne le sont pas — c'est ce qui autorise à s'en servir
    pour le prochain tirage."""
    cfg, tir, _ = jeux[cle]
    moitie = len(tir) // 2
    c1 = calibration_empirique(cfg, tir[:moitie])
    c2 = calibration_empirique(cfg, tir[moitie:])
    assert c1 and c2
    ns = list(nums(cfg))
    r = statistics.correlation([c1["gamma"][n] for n in ns],
                               [c2["gamma"][n] for n in ns])
    assert r > 0.75, f"gamma instable dans le temps (Pearson {r:+.3f})"


# ---------------------------------------------------------------------------
# 5. CO-OCCURRENCES — les features de grille sont estimées, plus devinées
# ---------------------------------------------------------------------------

def test_compter_paires_est_bien_un_compte_de_paires():
    assert compter_paires((1, 2, 3), lambda x, y: True) == 3      # C(3,2)
    assert compter_paires((1, 2, 3, 4, 5), lambda x, y: True) == 10
    assert compter_paires((1, 2, 3), lambda x, y: y - x == 1) == 2
    assert compter_paires((10, 20, 30), lambda x, y: y - x == 1) == 0
    # ordre indifférent : la fonction trie
    assert (compter_paires((5, 1, 3), lambda x, y: x < y)
            == compter_paires((1, 3, 5), lambda x, y: x < y) == 3)


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_les_co_occurrences_retenues_ont_le_meme_signe_dans_les_deux_jeux(
        cle, jeux):
    """Chaque feature n'a été gardée que parce qu'elle ressort dans DEUX jeux
    indépendants, de même signe. C'est ce qui la distingue d'un motif trouvé
    à force de chercher."""
    _, _, calib = jeux[cle]
    attendu = {"date_31": +1, "mois_12": +1, "consecutifs": -1,
               "meme_dizaine": -1, "hauts_31": +1}
    for nom, _ in PAIRES_POPULARITE:
        theta = calib["theta"][nom]
        se = calib["se_theta"][nom]
        assert abs(theta / se) > 3.0, f"{nom} : |t|={abs(theta/se):.2f} ≤ 3"
        assert math.copysign(1, theta) == attendu[nom], (
            f"{nom} : signe {theta:+.4f} contraire à l'autre jeu")


def test_les_regles_codees_en_dur_de_la_v23_etaient_inestimables():
    """Garde-fou historique. `popularite_grille_penalite` pénalisait de +1.0
    les grilles « tous ≤ 15 » et de +2.0 les suites arithmétiques. Aucune de
    ces deux formes n'a assez d'occurrences pour soutenir un coefficient :
    les retirer n'était pas un choix esthétique.
    """
    for cle, seuil in (("loto", 3), ("euromillions", 1)):
        tir = tirages_des_archives(cle)
        tous_bas = sum(1 for t in tir if all(x <= 15 for x in t["balls"]))
        arith = sum(1 for t in tir
                    if len({sorted(t["balls"])[k] - sorted(t["balls"])[k - 1]
                            for k in range(1, len(t["balls"]))}) == 1)
        assert tous_bas == 0, f"{cle} : « tous ≤ 15 » n'est plus à 0"
        assert arith < seuil, f"{cle} : {arith} suites arithmétiques"


# ---------------------------------------------------------------------------
# 6. INVARIANTS de la log-popularité et du multiplicateur de partage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_gamma_et_delta_sont_centres(cle, jeux):
    """Seuls les écarts relatifs ont un sens : le niveau est absorbé par les
    effets fixes de rang. Un gamma décentré décalerait tous les pop_rel."""
    cfg, _, calib = jeux[cle]
    assert statistics.mean(calib["gamma"].values()) == pytest.approx(0, abs=1e-9)
    assert statistics.mean(calib["delta"].values()) == pytest.approx(0, abs=1e-9)


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_une_grille_surjouee_partage_plus_qu_une_grille_delaissee(cle, jeux):
    """Le sens du multiplicateur, vérifié sur les extrêmes mesurés."""
    cfg, _, calib = jeux[cle]
    haut = tuple(sorted(calib["top_surjoues"][:cfg["pick"]]))
    bas = tuple(sorted(calib["top_delaisses"][:cfg["pick"]]))
    assert pop_rel_grille(cfg, haut, calib) > pop_rel_grille(cfg, bas, calib)
    assert popularite_log(cfg, haut, calib) > popularite_log(cfg, bas, calib)


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_empiler_les_numeros_delaisses_ne_donne_PAS_la_meilleure_grille(
        cle, jeux):
    """Le résultat contre-intuitif que la v2.3 ne pouvait pas voir.

    Prendre les 5 numéros les plus délaissés donne une grille entièrement
    au-dessus de 31. Or `hauts_31` est POSITIF et fort (+0,21 au Loto,
    +0,20 à l'EuroMillions) : les numéros hauts sont cochés ENSEMBLE, parce
    que seuls les joueurs « hors dates » les cochent. C'est la signature d'un
    mélange de populations, et cela veut dire que la grille tout-en-haut est
    elle-même encombrée — vraisemblablement par les gens qui suivent le même
    conseil anti-anniversaire que ce produit.

    Un modèle purement marginal (v2.3) ne peut pas voir cet effet et
    surestime donc le bénéfice de cette grille. On vérifie ici qu'au moins
    une grille fait mieux que l'empilement naïf des délaissés.
    """
    cfg, _, calib = jeux[cle]
    naif = tuple(sorted(calib["top_delaisses"][:cfg["pick"]]))
    assert all(n > 31 for n in naif), "le cas d'école n'est plus réuni"

    rng = random.Random(4242)
    mieux = [g for g in
             (tuple(sorted(rng.sample(range(1, cfg["n_max"] + 1), cfg["pick"])))
              for _ in range(4000))
             if popularite_log(cfg, g, calib)
             < popularite_log(cfg, naif, calib)]
    assert mieux, "aucune grille ne bat l'empilement des délaissés"


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_terme_de_co_occurrence_pese_autant_que_les_marginaux(cle, jeux):
    """Justifie que les co-occurrences ne soient pas un raffinement cosmétique.

    Sur des grilles tirées au hasard, la dispersion apportée par Σ theta·paires
    doit être du même ordre que celle de Σ gamma. Si elle était marginale, on
    pourrait s'en passer ; elle ne l'est pas.
    """
    cfg, _, calib = jeux[cle]
    rng = random.Random(99)
    g_marg, g_pair = [], []
    for _ in range(2000):
        b = tuple(sorted(rng.sample(range(1, cfg["n_max"] + 1), cfg["pick"])))
        g_marg.append(sum(calib["gamma"][x] for x in b))
        g_pair.append(sum(calib["theta"][nom] * v for (nom, _), v
                          in zip(PAIRES_POPULARITE, traits_paires(b),
                                 strict=True)))
    sd_m, sd_p = statistics.stdev(g_marg), statistics.stdev(g_pair)
    assert sd_p > 0.3 * sd_m, (
        f"co-occurrences négligeables (SD {sd_p:.3f} contre {sd_m:.3f})")


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_pop_rel_reste_borne_sur_toutes_les_grilles_extremes(cle, jeux):
    """L'EV divise par (1 + n_est·p1·pop_rel) : un pop_rel nul ou infini
    produirait une EV absurde. On balaie des grilles volontairement extrêmes.
    """
    cfg, _, calib = jeux[cle]
    rng = random.Random(7)
    cas = [tuple(range(1, cfg["pick"] + 1)),
           tuple(range(cfg["n_max"] - cfg["pick"] + 1, cfg["n_max"] + 1)),
           tuple(sorted(calib["top_surjoues"][:cfg["pick"]])),
           tuple(sorted(calib["top_delaisses"][:cfg["pick"]]))]
    cas += [tuple(sorted(rng.sample(range(1, cfg["n_max"] + 1), cfg["pick"])))
            for _ in range(300)]
    for balls in cas:
        v = pop_rel_grille(cfg, balls, calib)
        assert 0.05 <= v <= 20.0
        assert math.isfinite(v)


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_popularite_log_est_additive_et_reproductible(cle, jeux):
    """Invariant : Σ gamma + Σ theta·paires − normalisation."""
    cfg, _, calib = jeux[cle]
    rng = random.Random(11)
    for _ in range(200):
        balls = tuple(sorted(rng.sample(range(1, cfg["n_max"] + 1),
                                        cfg["pick"])))
        attendu = sum(calib["gamma"][b] for b in balls)
        attendu += sum(calib["theta"][nom] * v for (nom, _), v
                       in zip(PAIRES_POPULARITE, traits_paires(balls),
                              strict=True))
        attendu -= calib["log_norm_nums"]
        assert popularite_log(cfg, balls, calib) == pytest.approx(attendu)
        # l'ordre des numéros ne doit rien changer
        melange = list(balls)
        rng.shuffle(melange)
        assert popularite_log(cfg, tuple(melange), calib) == pytest.approx(
            attendu)


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_multiplicateur_de_partage_vaut_1_pour_une_grille_moyenne(cle, jeux):
    """L'invariant que la v2.4 a d'abord manqué, et qui compte pour l'EV.

    `ev_grille` calcule les co-gagnants par n_est · p1 · pop_rel. Comme p1 est
    déjà la probabilité d'une combinaison sous jeu uniforme, pop_rel ne doit
    porter QUE l'écart à l'uniforme — donc valoir 1 en moyenne sur des
    combinaisons tirées au hasard.

    Centrer gamma ne suffit PAS : cela centre le logarithme, et par Jensen
    E[exp(indice)] > exp(E[indice]) = 1. Avant correction, la grille médiane
    sortait à 1,48 et tous les partageurs attendus étaient doublés. À
    l'échelle de la v2.3 l'écart valait ~1 % et passait inaperçu ; à la bonne
    échelle il ne passe plus.
    """
    cfg, _, calib = jeux[cle]
    rng = random.Random(31337)
    vals = [pop_rel_grille(
        cfg, tuple(sorted(rng.sample(range(1, cfg["n_max"] + 1),
                                     cfg["pick"]))), calib)
        for _ in range(20_000)]
    moyenne = statistics.mean(vals)
    assert moyenne == pytest.approx(1.0, abs=0.03), (
        f"une grille moyenne partage ×{moyenne:.3f} au lieu de ×1 : "
        f"les co-gagnants attendus sont faux du même facteur")

    # et avec le bonus, la même chose doit tenir
    avec = [pop_rel_grille(
        cfg, tuple(sorted(rng.sample(range(1, cfg["n_max"] + 1), cfg["pick"]))),
        calib,
        bonus=rng.sample(list(bonus_nums(cfg)), cfg["bonus_pick"]))
        for _ in range(20_000)]
    assert statistics.mean(avec) == pytest.approx(1.0, abs=0.05)


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_bonus_entre_dans_la_popularite_quand_il_est_fourni(cle, jeux):
    cfg, _, calib = jeux[cle]
    balls = tuple(sorted(calib["top_delaisses"][:cfg["pick"]]))
    b_pop = max(bonus_nums(cfg), key=lambda n: calib["delta"][n])
    b_del = min(bonus_nums(cfg), key=lambda n: calib["delta"][n])
    assert (popularite_log(cfg, balls, calib, bonus=[b_pop])
            > popularite_log(cfg, balls, calib, bonus=[b_del]))


# ---------------------------------------------------------------------------
# 6bis. LA VALIDATION HORS ÉCHANTILLON QUI COMPTE VRAIMENT
# ---------------------------------------------------------------------------

def _poisson_irls(y, x, offset, tours=60):
    """log E[y] = a + b·x + offset. Rend (b, se_b)."""
    a = b = 0.0
    det = s00 = 0.0
    for _ in range(tours):
        s00 = s01 = s11 = g0 = g1 = 0.0
        for yi, xi, oi in zip(y, x, offset, strict=True):
            mu = math.exp(a + b * xi + oi)
            r = yi - mu
            g0 += r
            g1 += r * xi
            s00 += mu
            s01 += mu * xi
            s11 += mu * xi * xi
        det = s00 * s11 - s01 * s01
        if abs(det) < 1e-12:
            break
        da = (s11 * g0 - s01 * g1) / det
        db = (-s01 * g0 + s00 * g1) / det
        a, b = a + da, b + db
        if abs(da) + abs(db) < 1e-12:
            break
    return b, math.sqrt(s00 / det)


@pytest.mark.parametrize(
    ("cle", "rang_cible", "rang_offset"),
    [("loto", 2, 9), ("euromillions", 3, 13)], ids=IDS)
def test_pop_rel_predit_le_partage_reel_au_rang_cinq_bons_numeros(
        cle, rang_cible, rang_offset):
    """Le test qui confronte le multiplicateur de partage à la réalité.

    La calibration n'apprend que sur les rangs peuplés — au plus 4 bons
    numéros. Le rang à 5 bons numéros n'y entre jamais : il est trop creux.
    C'est donc un test HORS ÉCHANTILLON dans la seule dimension qui compte,
    puisque partager le jackpot c'est apparier les 5 boules.

    Et la prédiction est CHIFFRÉE, pas directionnelle. `popularite_log` est
    construit à l'échelle m = pick, donc

        log E[gagnants à 5 bons] = a + log(participation) + b × indice
        avec b = 1 − m_offset/pick

    soit 0,923 au Loto (rang 9, m = 0,385) et 0,600 à l'EuroMillions
    (rang 13, m = 2). L'échelle de la v2.3 aurait donné ~0,17 fois ces
    valeurs : le test la rejette explicitement.

    Apprentissage sur la 1re moitié, mesure sur la 2e — aucune fuite.
    """
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    moitie = len(tir) // 2
    calib = calibration_empirique(cfg, tir[:moitie])
    assert calib is not None
    assert rang_cible not in calib["rangs"], (
        f"le rang {rang_cible} sert à l'estimation : le test n'est plus "
        f"hors échantillon")

    y, x, off = [], [], []
    for t in tir[moitie:]:
        w, n = t["gagnants"].get(rang_cible), t["gagnants"].get(rang_offset)
        if w is None or not n:
            continue
        y.append(float(w))
        x.append(popularite_log(cfg, t["balls"], calib))
        off.append(math.log(n))
    assert sum(y) > 200, "trop peu d'événements de partage pour conclure"

    b, se = _poisson_irls(y, x, off)
    attendu = 1.0 - rangs_mb(cfg)[rang_offset][0] / cfg["pick"]
    assert abs(b) / se > 5, f"aucun effet de partage détecté (b={b:+.3f})"
    assert abs(b - attendu) / se < 3.0, (
        f"échelle fausse : mesuré {b:+.3f} ± {se:.3f}, "
        f"le modèle prédit {attendu:.3f} ({abs(b-attendu)/se:.1f} σ)")
    # et l'échelle de la v2.3 doit être franchement exclue
    assert abs(b - 0.17 * attendu) / se > 4.0, (
        "l'ancienne échelle n'est pas distinguable — la correction v2.4 "
        "ne serait pas justifiée")


# ---------------------------------------------------------------------------
# 7. REPLI quand l'historique est trop court
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_calibration_refuse_de_conclure_sur_trop_peu_de_tirages(cle, jeux):
    cfg, tir, _ = jeux[cle]
    assert calibration_empirique(cfg, tir[:MIN_TIRAGES_CALIBRATION - 1]) is None
    assert calibration_empirique(cfg, []) is None


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_sans_calibration_on_retombe_sur_le_prior_et_on_le_dit(cle, jeux):
    """Le repli doit rester utilisable ET s'annoncer comme tel : c'est ce qui
    empêche d'afficher « mesuré sur N tirages » quand rien ne l'a été."""
    cfg, _, _ = jeux[cle]
    scores, libelle = scores_anti(cfg, None)
    assert len(scores) == cfg["n_max"]
    assert "heuristique" in libelle
    assert set(scores_anti_bonus(cfg, None)) == set(bonus_nums(cfg))
    _, libelle_ok = scores_anti(cfg, calibration_empirique(
        cfg, tirages_des_archives(cle)))
    assert "heuristique" not in libelle_ok


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_score_anti_classe_a_l_inverse_de_la_popularite(cle, jeux):
    """Le numéro le plus joué doit recevoir le plus petit score anti-partage.
    Une inversion de signe ici retournerait le produit contre son utilisateur
    sans rien casser d'autre.
    """
    cfg, _, calib = jeux[cle]
    scores, _ = scores_anti(cfg, calib)
    plus_joue = max(nums(cfg), key=lambda n: calib["gamma"][n])
    moins_joue = min(nums(cfg), key=lambda n: calib["gamma"][n])
    assert scores[plus_joue] == pytest.approx(min(scores.values()))
    assert scores[moins_joue] == pytest.approx(max(scores.values()))

    b = scores_anti_bonus(cfg, calib)
    bp = max(bonus_nums(cfg), key=lambda n: calib["delta"][n])
    bm = min(bonus_nums(cfg), key=lambda n: calib["delta"][n])
    assert b[bp] == pytest.approx(min(b.values()))
    assert b[bm] == pytest.approx(max(b.values()))
