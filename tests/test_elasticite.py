"""Le partage joue à TOUS les rangs — et on exige la preuve, pas l'intuition.

Ce module est le garde-fou de la v2.8. Le moteur ne corrigeait le partage
qu'au rang 1, c'est-à-dire sur un gain réalisable une fois tous les 122 000
ans. La v2.8 le corrige à chaque rang, sur des gains encaissés une douzaine de
fois par an.

Une affirmation pareille ne vaut que par ce qui pourrait la démentir. Les
tests ci-dessous sont donc écrits pour ÉCHOUER si :

  · les rangs qui ne doivent pas réagir réagissent (placebo) ;
  · une popularité fabriquée au hasard produit le même effet (témoin) ;
  · l'effet ne survit pas hors échantillon ;
  · l'atténuation combinatoire est oubliée — l'erreur qui multiplierait le
    levier annoncé par ~2,5 au rang le plus lourd ;
  · le facteur de partage dérive d'une grille moyenne, ou n'est plus monotone.
"""
from __future__ import annotations

import math
import random
import statistics

import pytest
from test_rangs import tirages_des_archives

from oracle import (
    JEUX,
    MIN_OBS_RANG_ELASTICITE,
    MIN_TIRAGES_CALIBRATION,
    _mco,
    backtest_partage,
    calibration_empirique,
    charges_combinatoires,
    composantes_popularite,
    elasticite_rangs,
    ev_grille,
    facteur_partage,
    grille_extreme,
    parametres_ev,
    pop_rel_grille,
    rang_affluence,
    rangs_mb,
)

IDS = ["loto", "euromillions"]


@pytest.fixture(scope="module")
def jeux():
    """(cfg, tirages, calib, elasticité) par jeu — calculé une seule fois.

    La calibration et l'élasticité coûtent chacune un balayage de tout
    l'historique ; les recalculer par test ferait exploser la durée de la
    suite sans rien prouver de plus.
    """
    out = {}
    for cle in IDS:
        cfg = JEUX[cle]
        tir = tirages_des_archives(cle)
        calib = calibration_empirique(cfg, tir)
        out[cle] = (cfg, tir, calib, elasticite_rangs(cfg, tir, calib))
    return out


# ===========================================================================
# 1. LA MESURE EXISTE ET VA DANS LE SENS ANNONCÉ
# ===========================================================================

@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_les_rangs_courants_paient_moins_quand_la_combinaison_est_populaire(
        cle, jeux):
    """Le fait central de la v2.8, sur les rangs qui pèsent.

    Tous les rangs sont pari-mutuels : une part fixe de la cagnotte divisée
    par le nombre de gagnants. Quand une combinaison très jouée sort, ils sont
    nombreux à la partager, et le rapport tombe. On l'exige sur les rangs qui
    demandent au moins 2 bons numéros ET qui portent une part réelle de l'EV.
    """
    cfg, _, _, el = jeux[cle]
    mb = rangs_mb(cfg)
    lourds = [r for r in el["beta"]
              if el["n_obs"].get(r, 0) >= MIN_OBS_RANG_ELASTICITE
              and mb[r][0] >= 2 and el["parts"].get(r, 0.0) >= 0.02]
    assert len(lourds) >= 4, "trop peu de rangs exploitables pour conclure"
    for r in lourds:
        assert el["beta"][r] < 0, (
            f"rang {r} : beta = {el['beta'][r]:+.4f}, on attend un rapport "
            "qui BAISSE quand la combinaison sortie est populaire")
        assert el["t"][r] < -3.0, (
            f"rang {r} : t = {el['t'][r]:+.2f}, effet non concluant")


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_l_elasticite_croit_avec_le_nombre_de_bons_numeros_exiges(cle, jeux):
    """Prédiction signée du mécanisme, et non simple corrélation.

    Si l'effet vient bien du partage, un rang qui exige 5 bons numéros doit
    dépendre BEAUCOUP plus de la popularité de la combinaison qu'un rang qui
    n'en exige que 2 : il faut avoir coché davantage des mêmes numéros. On
    compare donc la moyenne des |beta| des rangs à m ≥ 4 à celle des rangs
    à m = 2. Aucun artefact d'affluence ne produirait cet ordre.
    """
    cfg, _, _, el = jeux[cle]
    mb = rangs_mb(cfg)
    def moyenne(seuil_bas, seuil_haut):
        vals = [abs(el["beta"][r]) for r in el["beta"]
                if el["n_obs"].get(r, 0) >= MIN_OBS_RANG_ELASTICITE
                and seuil_bas <= mb[r][0] <= seuil_haut
                and el["parts"].get(r, 0.0) >= 0.005]
        return statistics.mean(vals) if vals else None

    hauts, bas = moyenne(4, 5), moyenne(2, 2)
    assert hauts is not None and bas is not None
    assert hauts > bas * 1.5, (
        f"|beta| moyen à m≥4 = {hauts:.4f} contre {bas:.4f} à m=2 : "
        "l'effet ne croît pas avec le nombre de bons numéros exigés, "
        "le mécanisme du partage n'est donc pas ce qu'on mesure")


# ===========================================================================
# 2. LES RÉFUTATIONS — placebo, témoin, hors échantillon
# ===========================================================================

@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_placebo_par_permutation_la_methode_ne_fabrique_pas_l_effet(cle, jeux):
    """LE contrôle de cette section — et le seul qui puisse échouer.

    Le placebo de la première version prenait le rang à m ≈ 0 et vérifiait
    qu'il sortait à zéro. Au Loto ce rang paie 2,20 €, UNE seule valeur
    distincte sur 1474 tirages : son beta était nul par identité comptable.
    L'assertion ne pouvait pas échouer, et une tautologie était publiée comme
    une réfutation. C'est exactement le genre de test qui rassure sans rien
    garantir.

    Ici on permute les combinaisons entre tirages : chaque soir garde ses
    rapports, ses gagnants et son affluence, mais reçoit les boules d'un autre.
    La loi de la popularité est intacte, sa relation aux rapports est détruite,
    et la mesure entière est rejouée. On exige que le taux de faux positifs
    reste au voisinage des 5 % attendus, et surtout que le |t| maximal du bruit
    reste sans commune mesure avec ceux des vraies données (jusqu'à 87).
    """
    _, _, _, el = jeux[cle]
    pl = el["placebo"]
    assert pl and pl["n_coefficients"] >= 20, "placebo trop maigre pour juger"
    taux = pl["n_significatifs"] / pl["n_coefficients"]
    assert taux < 0.20, (
        f"{pl['n_significatifs']}/{pl['n_coefficients']} coefficients "
        f"significatifs sur des tirages MÉLANGÉS ({taux:.0%}) : la méthode "
        "fabrique du signal toute seule")
    assert pl["t_max"] < 5.0, (
        f"|t| max = {pl['t_max']} sur du bruit pur")
    vrais = [abs(el["t"][r]) for r in el["t"]
             if el["t"].get(r) and el["n_obs"].get(r, 0)
             >= MIN_OBS_RANG_ELASTICITE]
    assert max(vrais) > 4 * pl["t_max"], (
        f"les vraies données culminent à |t| = {max(vrais):.1f} contre "
        f"{pl['t_max']} pour le bruit : l'écart n'est pas assez net")


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_temoin_une_popularite_tiree_au_sort_ne_produit_aucun_effet(cle, jeux):
    """Si la méthode fabriquait l'effet toute seule, ceci le montrerait.

    On refait exactement la même régression, avec le même contrôle
    d'affluence, sur les mêmes rapports — mais en remplaçant la popularité par
    du bruit tiré au sort. Tout beta qui survivrait ici serait un artefact de
    la méthode, pas une propriété des tirages.
    """
    cfg, tir, _, el = jeux[cle]
    rng = random.Random(20260806)
    ra = el["rang_affluence"]
    mb = rangs_mb(cfg)
    testes = [r for r in el["beta"]
              if el["n_obs"].get(r, 0) >= MIN_OBS_RANG_ELASTICITE
              and mb[r][0] >= 2 and el["parts"].get(r, 0.0) >= 0.02]
    faux = [rng.gauss(0.0, 1.0) for _ in tir]
    for r in testes:
        ys, xs, aff = [], [], []
        for i, t in enumerate(tir):
            v = t["rapports"].get(r)
            a = t["gagnants"].get(ra, 0)
            if v and v > 0 and a > 0:
                ys.append(math.log(v))
                xs.append(faux[i])
                aff.append(math.log(a))
        coefs, ses = _mco([xs, aff], ys)
        t_temoin = coefs[0] / ses[0] if ses[0] > 1e-12 else 0.0
        assert abs(t_temoin) < 3.0, (
            f"rang {r} : une popularité TIRÉE AU SORT sort à t = "
            f"{t_temoin:+.2f} — la méthode fabrique l'effet toute seule")


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_hors_echantillon_l_effet_survit_sur_des_tirages_jamais_vus(cle, jeux):
    """Le standard du projet : appris sur le passé, jugé sur l'inconnu.

    Calibration ET élasticité apprises sur les 70 % les plus anciens ; le
    signe est ensuite exigé sur les 30 % restants, jamais vus, en réutilisant
    la calibration du passé (sans quoi on referait entrer le futur par la
    fenêtre).
    """
    cfg, tir, _, _ = jeux[cle]
    coupe = int(len(tir) * 0.7)
    passe, futur = tir[:coupe], tir[coupe:]
    assert len(passe) >= MIN_TIRAGES_CALIBRATION

    calib_passe = calibration_empirique(cfg, passe)
    el_passe = elasticite_rangs(cfg, passe, calib_passe)
    # le futur, mesuré AVEC la calibration du passé : rien du futur n'entre
    el_futur = elasticite_rangs(cfg, futur, calib_passe)
    assert el_passe and el_futur

    mb = rangs_mb(cfg)
    communs = [r for r in el_passe["beta"]
               if el_passe["n_obs"].get(r, 0) >= MIN_OBS_RANG_ELASTICITE
               and el_futur["n_obs"].get(r, 0) >= 40
               and mb[r][0] >= 2 and el_passe["parts"].get(r, 0.0) >= 0.02]
    assert len(communs) >= 3
    for r in communs:
        assert el_futur["beta"][r] < 0, (
            f"rang {r} : beta appris {el_passe['beta'][r]:+.4f} mais "
            f"{el_futur['beta'][r]:+.4f} sur les tirages jamais vus — "
            "l'effet ne survit pas hors échantillon")


# ===========================================================================
# 3. L'ATTÉNUATION COMBINATOIRE — l'erreur qui gonflerait le levier
# ===========================================================================

@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_les_charges_reproduisent_le_tirage_reel_a_chaque_m(cle, jeux):
    """La vérification que la v2.8 initiale n'avait pas — et qui a démasqué
    une charge fausse d'un facteur 1,83.

    On simule vraiment : on tire des combinaisons au hasard, on les range par
    nombre de numéros communs avec une grille donnée, et on compare la
    popularité MOYENNE observée à ce que `charges_combinatoires` prédit.

    Le premier jet ne vérifiait ce point qu'en m = pick. Or le terme qui
    manquait — celui du complémentaire de la grille — s'annule identiquement
    en m = pick. Le contrôle était structurellement aveugle à l'erreur qu'il
    était censé attraper. Ici on couvre TOUS les m.
    """
    cfg, _, calib, el = jeux[cle]
    g = grille_extreme(cfg, calib, -1)
    comp = composantes_popularite(cfg, g, calib)
    ecarts = [c - r for c, r in zip(comp, el["refs"], strict=True)]

    rng = random.Random(987)
    univers = list(range(1, cfg["n_max"] + 1))
    par_m: dict[int, list[float]] = {}
    for _ in range(120_000):
        d = tuple(sorted(rng.sample(univers, cfg["pick"])))
        m = len(set(d) & set(g))
        c = composantes_popularite(cfg, d, calib)
        par_m.setdefault(m, []).append(c[0] + c[1])

    ref_p = el["refs"][0] + el["refs"][1]
    testes = 0
    for m, vals in sorted(par_m.items()):
        if len(vals) < 1000:
            continue
        testes += 1
        mesure = statistics.mean(vals) - ref_p
        charges = charges_combinatoires(cfg, float(m))
        predit = sum(c * e for c, e in zip(charges, ecarts, strict=True))
        assert mesure == pytest.approx(predit, abs=0.02), (
            f"m={m} : popularité moyenne mesurée {mesure:+.4f}, prédite "
            f"{predit:+.4f} — les charges combinatoires sont fausses")
    assert testes >= 3, "pas assez de valeurs de m couvertes"


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_l_attenuation_est_appliquee_et_bride_bien_le_levier(cle, jeux):
    """Sans atténuation, le levier annoncé serait surestimé d'un facteur
    ~pick/m sur le rang qui pèse le plus (au Loto : 2,5).

    On vérifie que le facteur de partage EST atténué, en le comparant à ce
    qu'il vaudrait si l'on appliquait bêtement beta à la popularité entière de
    la grille. La version atténuée doit être strictement plus modeste.
    """
    cfg, _, calib, el = jeux[cle]
    g = grille_extreme(cfg, calib, -1)
    comp = composantes_popularite(cfg, g, calib)
    attenue = facteur_partage(el, comp)

    # la même chose SANS atténuation : toutes les charges forcées à 1
    brut = dict(el, charges=dict.fromkeys(el["charges"], (1.0, 1.0, 0.0)))
    naif = facteur_partage(brut, comp)

    assert attenue > 1.0, "une grille délaissée doit encaisser davantage"
    assert attenue < naif, (
        f"facteur atténué {attenue:.4f} ≥ facteur naïf {naif:.4f} : "
        "l'atténuation combinatoire n'est pas appliquée")


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_les_charges_decroissent_avec_le_nombre_de_bons_numeros(cle, jeux):
    """Moins on trouve de numéros, moins la combinaison sortie doit à la
    grille jouée — donc moins la popularité de celle-ci se transmet.

    La charge marginale devient NÉGATIVE sous m = 1, et ce n'est pas une
    anomalie : si l'on a trouvé zéro numéro, le tirage est entièrement composé
    de numéros hors de la grille, dont la popularité est l'opposée de la
    sienne. Une version antérieure de ce test verrouillait `charge ≥ 0` et
    aurait donc rejeté la formule correcte.
    """
    cfg, _, _, _ = jeux[cle]
    precedent = None
    for m in range(cfg["pick"], 0, -1):
        marg, p_in, _ = charges_combinatoires(cfg, m)
        assert marg <= 1.0 and p_in <= 1.0
        if precedent is not None:
            assert marg < precedent[0]
            assert p_in <= precedent[1]
        precedent = (marg, p_in)
    assert charges_combinatoires(cfg, cfg["pick"])[0] == pytest.approx(1.0)
    assert charges_combinatoires(cfg, cfg["pick"])[1] == pytest.approx(1.0)
    assert charges_combinatoires(cfg, 0.0)[0] < 0.0, (
        "à zéro bon numéro, le tirage est fait des numéros que la grille "
        "n'a PAS : sa popularité doit être l'opposée de celle de la grille")


# ===========================================================================
# 4. LE FACTEUR DE PARTAGE — invariants d'usage
# ===========================================================================

@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_facteur_vaut_un_pour_une_grille_quelconque(cle, jeux):
    """L'invariant d'ancrage : `ev_fixe` est le gain d'une grille quelconque.
    Si le facteur en dérivait, toute l'EV serait décalée du même biais.
    """
    cfg, _, calib, el = jeux[cle]
    rng = random.Random(4242)
    vals = [facteur_partage(el, composantes_popularite(
        cfg, tuple(sorted(rng.sample(range(1, cfg["n_max"] + 1),
                                     cfg["pick"]))), calib))
        for _ in range(3000)]
    moyenne = statistics.mean(vals)
    assert moyenne == pytest.approx(1.0, abs=0.03), (
        f"une grille quelconque encaisse ×{moyenne:.4f} au lieu de ×1")


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_moins_la_grille_est_jouee_plus_elle_encaisse(cle, jeux):
    """Monotonie. C'est toute la thèse du produit en une assertion."""
    cfg, _, calib, el = jeux[cle]
    delaissee = facteur_partage(el, composantes_popularite(
        cfg, grille_extreme(cfg, calib, -1), calib))
    populaire = facteur_partage(el, composantes_popularite(
        cfg, grille_extreme(cfg, calib, +1), calib))
    assert delaissee > 1.0 > populaire, (
        f"délaissée ×{delaissee:.4f}, populaire ×{populaire:.4f}")


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_bridage_prudent_annonce_moins_que_l_extrapolation(cle, jeux):
    """Les grilles extrêmes du mode « anti » sortent du domaine observé.

    Le moteur doit alors annoncer le chiffre BRIDÉ, jamais l'extrapolation.
    Ce test vérifie à la fois que le bridage mord et qu'il mord dans le bon
    sens.
    """
    cfg, _, calib, el = jeux[cle]
    g = grille_extreme(cfg, calib, -1)
    comp = composantes_popularite(cfg, g, calib)
    total = comp[0] + comp[1] - el["refs"][0] - el["refs"][1]
    assert total < el["plancher"], (
        "la grille la moins jouée devrait sortir du domaine observé ; "
        "si ce n'est plus le cas, ce test ne mesure plus rien")
    assert facteur_partage(el, comp, prudent=True) < facteur_partage(
        el, comp, prudent=False)


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_bridage_mord_aussi_du_cote_des_grilles_tres_jouees(cle, jeux):
    """Le bord haut du domaine doit être bridé comme le bord bas.

    Le premier jet ne conditionnait pas le bord haut à `prudent` : les deux
    versions devenaient alors rigoureusement identiques sur les grilles très
    jouées, et l'export annonçait un « extrapolé » qui n'extrapolait rien.
    Muet exactement là où l'écart entre mesure et modèle se lit le mieux.
    """
    cfg, _, calib, el = jeux[cle]
    g = grille_extreme(cfg, calib, +1)
    comp = composantes_popularite(cfg, g, calib)
    total = comp[0] + comp[1] - el["refs"][0] - el["refs"][1]
    assert total > el["plafond"], (
        "la grille la plus jouée devrait sortir du domaine observé")
    pru = facteur_partage(el, comp, prudent=True)
    ext = facteur_partage(el, comp, prudent=False)
    assert ext < pru, (
        f"prudent ×{pru:.4f} et extrapolé ×{ext:.4f} : le bord haut n'est pas "
        "bridé, les deux chiffres racontent la même chose")
    assert ext < 1.0


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_sans_elasticite_l_ev_retombe_exactement_sur_l_ancien_calcul(cle, jeux):
    """Dégradation propre : pas d'élasticité, pas de correction inventée.

    Un historique trop court, une calibration absente, et le moteur doit
    rendre le chiffre v2.7 — pas une approximation silencieuse.
    """
    cfg, tir, calib, el = jeux[cle]
    ev_p = parametres_ev(cfg, tir)
    g = grille_extreme(cfg, calib, -1)
    pr = pop_rel_grille(cfg, g, calib)
    jackpot = 10_000_000.0
    sans = ev_grille(ev_p, jackpot, pr)
    attendu = (ev_p["ev_fixe"] + sans["comp_jackpot"] - cfg["prix"])
    assert sans["ev"] == pytest.approx(attendu, abs=1e-4)
    assert sans["facteur_partage"] == 1.0
    # et sans `composantes`, même avec une élasticité fournie
    assert ev_grille(ev_p, jackpot, pr, el)["facteur_partage"] == 1.0


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_l_ev_reste_negative_aux_jackpots_courants(cle, jeux):
    """Le garde-fou : au jackpot ordinaire, aucun levier ne rend le jeu bon.

    « Ordinaire » veut dire le jackpot de départ et ses premiers reports —
    l'écrasante majorité des tirages. Le levier du partage y récupère quelques
    points sur la maison sans jamais inverser le signe.
    """
    cfg, tir, calib, el = jeux[cle]
    ev_p = parametres_ev(cfg, tir)
    g = grille_extreme(cfg, calib, -1)
    comp = composantes_popularite(cfg, g, calib)
    pr = pop_rel_grille(cfg, g, calib)
    courants = ((2_000_000.0, 15_000_000.0) if cfg["pick"] == 5
                and cfg["n_max"] == 49 else (17_000_000.0, 100_000_000.0))
    for jackpot in courants:
        ev = ev_grille(ev_p, jackpot, pr, el, comp)
        assert ev["ev"] < 0, (
            f"EV = {ev['ev']:+.4f} € à {jackpot:,.0f} € de jackpot : "
            "le moteur annonce un jeu rentable")
        assert ev["ev_extrapole"] < 0
        assert ev["alerte"] is None


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_une_ev_positive_ne_sort_jamais_sans_son_avertissement(cle, jeux):
    """Ce que le premier jet de ce module croyait impossible.

    À très gros report, l'arithmétique donne bel et bien une espérance
    positive — au Loto, vers 26 M€ pour une grille délaissée. Le moteur ne
    doit alors NI le cacher, NI le publier nu : `n_est` est la participation
    médiane des 160 derniers tirages, or c'est précisément la foule qui
    explose ces soirs-là. Le chiffre est un majorant, et il doit le dire.
    """
    cfg, tir, calib, el = jeux[cle]
    ev_p = parametres_ev(cfg, tir)
    g = grille_extreme(cfg, calib, -1)
    comp = composantes_popularite(cfg, g, calib)
    pr = pop_rel_grille(cfg, g, calib)
    # un jackpot volontairement démesuré : on force le régime à tester
    enorme = 40.0 * ev_p["p_jackpot_inv"] * cfg["prix"]
    ev = ev_grille(ev_p, enorme, pr, el, comp)
    assert ev["ev"] > 0, "le régime à EV positive n'est plus atteignable"
    assert ev["alerte"], (
        "EV positive publiée sans avertissement : le lecteur y lirait un "
        "conseil de jeu")
    assert "participation" in ev["alerte"].lower()


# ===========================================================================
# 5. LE BACKTEST APPARIÉ — la mesure sans modèle
# ===========================================================================

@pytest.fixture(scope="module")
def partage():
    """Backtest apparié sur les 600 derniers tirages de chaque jeu.

    600 plutôt que tout l'historique : la calibration se recalcule tous les
    25 tirages et c'est elle qui coûte. 600 laisse assez d'événements gagnants
    (une quarantaine au rang le plus peuplé) pour que les assertions aient un
    sens, sans faire de ce module le poste le plus lent de la suite.
    """
    return {cle: backtest_partage(JEUX[cle], tirages_des_archives(cle),
                                  n_derniers=600, seed=0)
            for cle in IDS}


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_backtest_apparie_mesure_bien_ce_qu_il_annonce(cle, partage):
    """Structure : quatre stratégies réglées sur les mêmes tirages, et un
    rang placebo désigné."""
    bp = partage[cle]
    assert bp is not None and bp["n_tirages"] >= 300
    assert set(bp["strategies"]) == {"anti", "anti_boules", "hasard",
                                     "anniversaire", "populaire"}
    assert bp["rang_placebo"] == rang_affluence(
        JEUX[cle], tirages_des_archives(cle))
    for d in bp["strategies"].values():
        assert d["n_grilles"] > 0
        assert d["rangs"], "aucun rang touché : le backtest ne mesure rien"


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_la_grille_delaissee_encaisse_plus_que_la_grille_populaire(
        cle, partage):
    """Le résultat, mesuré SANS le modèle d'élasticité.

    On ne compare pas des ROI — le projet a montré qu'ils sont dominés par la
    chance. On compare le rapport encaissé sachant qu'un rang a été touché.
    La probabilité de toucher, elle, ne dépend d'aucune stratégie.
    """
    s = partage[cle]["strategies"]
    assert s["anti"]["surcote_pct"] > s["populaire"]["surcote_pct"], (
        f"anti {s['anti']['surcote_pct']:+.2f} % contre populaire "
        f"{s['populaire']['surcote_pct']:+.2f} % : le levier ne se voit pas")
    assert s["anti"]["surcote_pct"] > 0, (
        "la grille délaissée n'encaisse pas plus que le rapport moyen")
    assert s["populaire"]["surcote_pct"] < 0


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_temoin_hasard_ne_surcote_pas(cle, partage):
    """Le témoin du backtest. Une grille tirée au sort touche les rangs de
    façon quelconque : elle doit encaisser le rapport moyen, ni plus ni moins.
    S'il dérivait, c'est l'estimateur qui serait biaisé, pas la stratégie qui
    serait bonne.
    """
    d = partage[cle]["strategies"]["hasard"]
    assert abs(d["surcote_pct"]) < 4.0, (
        f"le témoin « hasard » surcote de {d['surcote_pct']:+.2f} % — "
        "l'estimateur est biaisé")


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_rang_placebo_ne_distingue_aucune_strategie(cle, partage):
    """Sur le rang insensible aux boules, les stratégies qui ne jouent QUE les
    boules doivent encaisser la même chose. C'est la contre-épreuve interne.

    Restreint aux stratégies à bonus neutre, et ce n'est pas un aménagement de
    confort : le rang placebo de l'EuroMillions exige les DEUX étoiles. Une
    stratégie qui choisit aussi ses étoiles y bouge de 40 %, pour une raison
    qui n'a rien à voir avec les boules. C'est ce test qui l'a révélé.
    """
    bp = partage[cle]
    ra = bp["rang_placebo"]
    mesures = 0
    for nom, d in bp["strategies"].items():
        v = d["rangs"].get(ra)
        if not v or not d["bonus_neutre"]:
            continue
        mesures += 1
        assert abs(v["ecart_pct"]) < 5.0, (
            f"{nom} : le rang placebo {ra} écarte de {v['ecart_pct']:+.2f} % — "
            "le backtest distingue des stratégies là où rien ne le permet")
    if mesures == 0:
        # C'est le cas du Loto : son seul rang à m ≈ 0 paie un prix FIXE
        # (2,20 €), il est donc écarté des références et ne peut servir de
        # placebo. Le contrôle du backtest est alors la stratégie « hasard »,
        # vérifiée par le test suivant, et le placebo par permutation vit du
        # côté de l'élasticité. On l'affirme plutôt que de le laisser passer
        # en silence pour un test qui n'aurait rien mesuré.
        assert not any(d["rangs"].get(ra)
                       for d in bp["strategies"].values())


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_le_bonus_est_un_levier_a_part_entiere(cle, partage):
    """La seconde découverte de ce module : le n° Chance et les Étoiles ont
    leur propre popularité, et elle rapporte.

    `anti` et `anti_boules` jouent EXACTEMENT les mêmes boules ; seul le bonus
    change (délaissé contre tiré au sort). Tout écart entre les deux ne peut
    donc venir que de là.
    """
    bp = partage[cle]
    s = bp["strategies"]
    assert bp["levier_bonus_pct"] is not None
    assert s["anti"]["surcote_pct"] > s["anti_boules"]["surcote_pct"], (
        f"choisir le {JEUX[cle]['bonus_nom']} délaissé ne rapporte rien "
        f"({s['anti']['surcote_pct']:+.2f} % contre "
        f"{s['anti_boules']['surcote_pct']:+.2f} %)")
    assert s["anti_boules"]["surcote_pct"] > 0, (
        "les boules seules doivent déjà rapporter, sans l'aide du bonus")


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_les_dates_d_anniversaire_coutent_de_l_argent(cle, partage):
    """Le seul conseil actionnable que ce projet ait jamais produit.

    Jouer cinq numéros ≤ 31 ne change pas la probabilité de gagner. Cela
    change le montant, parce que la France entière joue des dates.
    """
    s = partage[cle]["strategies"]
    assert s["anniversaire"]["surcote_pct"] < 0, (
        "les grilles d'anniversaire devraient encaisser MOINS que la moyenne")
    assert s["anniversaire"]["surcote_pct"] < s["hasard"]["surcote_pct"]


@pytest.mark.parametrize("cle", IDS, ids=IDS)
def test_la_mesure_et_le_modele_racontent_la_meme_histoire(cle, jeux, partage):
    """La confrontation qui compte : le backtest sans modèle contre la
    prédiction du modèle d'élasticité.

    Ils n'ont aucune raison de coïncider au centième — le backtest joue des
    grilles gloutonnes sur 400 tirages, le modèle intègre sur toute la loi.
    Mais ils doivent s'accorder sur le SIGNE et sur l'ordre de grandeur. Un
    désaccord franc signifierait que l'atténuation combinatoire est mal posée,
    et c'est le backtest qui aurait raison.
    """
    cfg, _, calib, el = jeux[cle]
    mesure = partage[cle]["strategies"]["anti"]["surcote_pct"]
    predit = 100 * (facteur_partage(el, composantes_popularite(
        cfg, grille_extreme(cfg, calib, -1), calib)) - 1.0)
    assert mesure > 0 and predit > 0
    assert 0.6 < mesure / predit < 2.0, (
        f"le backtest mesure {mesure:+.2f} % là où le modèle prédit "
        f"{predit:+.2f} % : les deux ne décrivent pas le même monde")
