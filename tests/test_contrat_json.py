"""Le contrat JSON consommé par docs/index.html (et demain par l'app Next.js).

Ces tests lisent les exports RÉELS commités dans docs/. Ils échouent si une
évolution du moteur casse la page sans bump de `meta.version`.

Ils vérifient aussi que les garde-fous du produit survivent à l'export : le
backtest présent, l'EV honnête (négative), le grand livre non masqué.
"""
from __future__ import annotations

import json
import math

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
    # 2.4 : la calibration anti-partage passe au panel à effets fixes de
    # tirage. `calibration.beta` change de SENS (log-popularité à l'échelle du
    # rang 1, ~5,4× les beta 2.3) et trois clés apparaissent. Le bump n'est pas
    # une formalité : une page 2.3 lisant un export 2.4 afficherait des
    # multiplicateurs de partage faux d'un facteur 5.
    # 2.5 : le seuil des dizaines passe de 3 à 2 dans `grille_valide` (les
    # grilles publiées changent) et la simulation expose `dispersion_anti`.
    # 2.6 : la page gagne la section « les numéros qui sortent le plus »,
    # alimentée par verdicts.frequences.
    # 2.7 : la page montre l'historique COMPLET tirage par tirage
    # (simulation.modes.*.tirages, perdants compris) et chaque grille affichée
    # est réglée contre le dernier tirage réel (vs_dernier).
    # 2.8 : le partage cesse d'être corrigé au seul rang 1. `elasticite`
    # apparaît, les grilles gagnent `pop_comp` (les deux composantes de la
    # popularité, qui ne portent pas la même charge vers un rang donné), et
    # `verdicts.valeur_modes` chiffre ce que chaque mode vaut — le mode
    # « pronostic » y sort NÉGATIF. Une page 2.7 lisant un export 2.8
    # continuerait d'annoncer un levier ~3 fois trop faible, et présenterait
    # « pronostic » comme un choix neutre.
    assert m["version"] == "2.8"
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


def test_calibration_v24_expose_de_quoi_juger_sa_solidite(export):
    """La page affiche « mesuré sur N tirages ». Elle doit pouvoir dire
    AVEC QUELLE FORCE, sinon la phrase n'engage à rien."""
    cal, m = export["calibration"], export["meta"]
    assert cal["t_median"] > 3, "signal trop faible pour piloter des grilles"
    assert cal["n_significatifs"] >= 0.7 * m["n_max"]
    assert len(cal["rangs_utilises"]) >= 2, (
        "un seul rang ne permet aucun effet fixe de tirage")
    assert len(cal["delta"]) == m["bonus_max"], "popularité du bonus absente"
    assert set(cal["theta"]) == {"date_31", "mois_12", "consecutifs",
                                 "meme_dizaine", "hauts_31"}


def test_les_co_occurrences_gardent_le_signe_mesure(export):
    """Un changement de signe ici inverserait le conseil donné : par exemple
    recommander des numéros consécutifs alors qu'ils sont sous-joués, ou
    l'inverse. Les signes sont ceux mesurés indépendamment sur les deux jeux.
    """
    t = export["calibration"]["theta"]
    assert t["date_31"] > 0 and t["mois_12"] > 0
    assert t["consecutifs"] < 0 and t["meme_dizaine"] < 0
    assert t["hauts_31"] > 0, (
        "hauts_31 doit rester positif : les grands numéros sont joués "
        "ensemble, une grille tout-en-haut est plus partagée qu'il n'y paraît")


def test_pop_rel_des_grilles_publiees_est_bien_inferieur_a_un(export):
    """Le produit promet des grilles MOINS partagées. En mode anti, cela doit
    se voir sur le multiplicateur exporté, sinon la promesse est verbale."""
    for g in export["modes"]["anti"]["grilles"]:
        assert g["pop_rel"] < 1.0, (
            f"grille {g['numeros']} annoncée anti-partage avec "
            f"pop_rel={g['pop_rel']} ≥ 1")


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
    le fait la page, au jackpot exporté.

    v2.8 — les gains hors jackpot ne sont plus une constante : ils sont
    multipliés par le facteur de partage de la grille. On le RECALCULE ici à
    partir du seul JSON, exactement comme le moteur — ce qui vérifie du même
    coup que l'export contient tout ce qu'il faut pour reproduire son propre
    chiffre. Un majorant grossier passerait à côté des deux.
    """
    e, m = export["ev_params"], export["meta"]
    jackpot = m["jackpot"] or 0
    p1 = 1 / e["p_jackpot_inv"]
    el = export.get("elasticite")
    for g in export["modes"]["anti"]["grilles"]:
        partage = e["n_est"] * p1 * g["pop_rel"]
        ev = ((e["ev_fixe"] or 0) * facteur_depuis_export(el, g["pop_comp"])
              + p1 * jackpot / (1 + partage) - e["prix"])
        assert ev < 0, "EV positive affichée — vérifier avant de publier"


def facteur_depuis_export(el, pop_comp):
    """Rejoue `facteur_partage` à partir du JSON seul.

    Si cette fonction ne peut plus être écrite, c'est que l'export a cessé de
    porter de quoi reproduire l'EV qu'il annonce — et la page se mettrait à
    afficher un nombre qu'elle ne sait plus justifier.
    """
    if not el:
        return 1.0
    ecarts = [c - r for c, r in zip(pop_comp, el["references"], strict=True)]
    total = ecarts[0] + ecarts[1]
    dom = el["domaine_observe"]
    borne = (dom["plancher"] if total < dom["plancher"]
             else dom["plafond"] if total > dom["plafond"] else None)
    if borne is not None and abs(total) > 1e-12:
        k = borne / total
        ecarts = [x * k for x in ecarts]
    f = 0.0
    for r in el["rangs"].values():
        charges = (r["charge_marginale"], r["charge_paires"],
                   r["charge_paires_croisees"])
        dp = sum(c * x for c, x in zip(charges, ecarts, strict=True))
        f += r["part_ev"] * math.exp(r["beta"] * dp)
    # les rangs à prix fixe entrent avec un facteur de 1 : rien ne les bouge
    return f + sum(el["rangs_a_prix_fixe"].values())


# ---- v2.8 : le partage joue à tous les rangs -------------------------------

def test_l_elasticite_est_exportee_avec_son_placebo(export):
    """Le levier du produit est désormais chiffré rang par rang. La page doit
    pouvoir l'afficher AVEC sa réfutation : sans le placebo publié à côté,
    c'est une promesse, pas une mesure."""
    el = export["elasticite"]
    assert el, "élasticité absente de l'export"
    for cle in ("n_tirages", "rang_affluence", "references",
                "domaine_observe", "placebo", "rangs", "rangs_a_prix_fixe",
                "note"):
        assert cle in el, cle
    assert len(el["references"]) == 3
    assert el["n_tirages"] >= 200
    assert el["rangs"], "aucun rang exploitable"
    for r in el["rangs"].values():
        for cle in ("m", "beta", "charge_marginale", "charge_paires",
                    "charge_paires_croisees", "part_ev", "n_obs",
                    "depuis_tirage", "prix_fixe"):
            assert cle in r, cle
        assert r["charge_marginale"] <= 1.0
        assert r["charge_paires"] <= 1.0


def test_le_placebo_publie_ne_conclut_a_rien(export):
    """Garde-fou : la mesure rejouée sur des tirages MÉLANGÉS ne doit rien
    trouver. C'est le seul contrôle de cette section qui puisse échouer — le
    précédent (« le rang à prix fixe sort à zéro ») était une tautologie, son
    rang ne prenant qu'une seule valeur sur tout l'historique."""
    pl = export["elasticite"]["placebo"]
    assert pl is not None
    for cle in ("n_essais", "n_coefficients", "n_significatifs", "t_max",
                "methode"):
        assert cle in pl, cle
    assert pl["n_coefficients"] >= 20
    assert pl["n_significatifs"] / pl["n_coefficients"] < 0.20, (
        f"{pl['n_significatifs']}/{pl['n_coefficients']} significatifs sur du "
        "bruit : la méthode fabrique l'effet toute seule")
    assert pl["t_max"] < 5.0


def test_l_elasticite_est_estimee_sur_le_bon_regime_de_gains(export):
    """Avant le 04/11/2019, les rangs du Loto payaient un montant FIXE — 417
    tirages sans la moindre élasticité possible. Les inclure raboterait toutes
    les pentes de 28 %. L'export doit montrer la fenêtre retenue par rang."""
    el = export["elasticite"]
    for r in el["rangs"].values():
        assert r["depuis_tirage"] is not None
        assert 0 <= r["depuis_tirage"] < el["n_tirages"]
        assert r["n_obs"] <= el["n_tirages"] - r["depuis_tirage"]


def test_les_grilles_portent_les_composantes_de_popularite(export):
    """`pop_comp` = (marginal, paires internes, paires croisées). Elles ne se
    transmettent pas de la même façon à un rang donné ; les fusionner
    surestimerait le levier. La page a besoin des trois pour recalculer l'EV
    comme le moteur — `facteur_partage` les zippe strictement avec ses charges
    à trois termes."""
    for mode in ("anti", "hybride", "pronostic"):
        for g in export["modes"][mode]["grilles"]:
            assert "pop_comp" in g, mode
            assert len(g["pop_comp"]) == 3
            assert all(isinstance(v, (int, float)) for v in g["pop_comp"])


def test_le_mode_pronostic_est_chiffre_et_sort_negatif(export):
    """Garde-fou v2.8, et le plus inconfortable : le produit doit publier ce
    que coûte son propre mode « folklore ».

    Le mode « pronostic » joue les numéros que tout le monde joue : il partage
    davantage, donc il détruit de la valeur. Le taire reviendrait à vendre du
    folklore sans son étiquette de prix.
    """
    vm = export["verdicts"]["valeur_modes"]
    assert vm, "valeur_modes absente — le lecteur ne sait pas ce qu'il choisit"
    for mode in ("anti", "hybride", "pronostic"):
        assert mode in vm
        for cle in ("gain_euro", "gain_pct_mise", "pop_rel_moyen"):
            assert cle in vm[mode], cle
    assert vm["anti"]["gain_pct_mise"] > 0
    assert vm["hybride"]["gain_pct_mise"] > 0
    assert vm["pronostic"]["gain_pct_mise"] < 0, (
        "le mode pronostic est annoncé comme neutre ou gagnant : il joue "
        "pourtant les numéros les plus partagés")
    # `anti` et `hybride` sont proches par construction — « hybride » pèse déjà
    # l'anti-partage à 70 %. Leur ORDRE peut donc s'inverser d'un dixième de
    # point selon la graine du générateur de grilles, et l'exiger serait un
    # test qui rougit sur du bruit. Ce qui doit tenir, c'est la structure :
    # `anti` joue bien les grilles les moins populaires.
    assert vm["anti"]["pop_rel_moyen"] <= vm["hybride"]["pop_rel_moyen"]
    assert vm["anti"]["pop_rel_moyen"] < vm["pronostic"]["pop_rel_moyen"]
    for mode in ("anti", "hybride"):
        assert (vm[mode]["gain_pct_mise"]
                > vm["pronostic"]["gain_pct_mise"] + 1.0)


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


def test_le_tableau_des_frequences_est_exporte(export):
    """v2.6 — « quels numéros sortent le plus ? » est LA question qu'on pose à
    un site de loto. La page doit y répondre, mais jamais sans sa référence :
    un classement seul induit en erreur, puisqu'il y a forcément un premier.

    On exige donc les trois éléments ensemble. Publier le classement sans la
    plage du hasard, ou sans l'épreuve en euros, serait exactement ce que font
    les sites de « numéros chauds ».
    """
    f = export["verdicts"]["frequences"]
    assert f, "tableau des fréquences absent"
    assert len(f["plus_sortis"]) >= 5 and len(f["moins_sortis"]) >= 5
    assert f["paires_frequentes"], "paires absentes"

    # la référence honnête
    assert f["record_hasard_min"] < f["record_hasard_max"]
    assert f["plus_sortis"][0]["sorties"] <= f["record_hasard_max"], (
        "le numéro le plus sorti dépasse ce qu'une machine parfaite produit — "
        "à vérifier sérieusement avant de publier")

    # l'épreuve des faits, sans laquelle le tableau est trompeur
    e = f["epreuve"]
    assert e["n_tirages"] >= 100 and e["mise"] > 0
    assert e["gain_hasard_min"] <= e["gain_hasard_median"] <= e["gain_hasard_max"]
    assert e["gain_numeros_chauds"] < e["mise"], (
        "jouer les numéros chauds serait rentable — invraisemblable, "
        "vérifier les données avant de publier ça")


def test_la_simulation_expose_sa_dispersion(export):
    """v2.5 — « si tu avais joué » affichait un nombre unique là où la loi
    s'étale d'un facteur 25 à 27 selon la graine du générateur. Ce n'est pas
    une mesure, c'est un échantillon de taille 1. La page a besoin de
    l'intervalle pour cesser de suggérer une précision qui n'existe pas.
    """
    d = export["simulation"]["dispersion_anti"]
    assert d, "dispersion absente — la page afficherait un nombre nu"
    assert d["n_graines"] >= 5
    assert d["min"] <= d["p10"] <= d["mediane"] <= d["p90"] <= d["max"]
    assert d["min"] >= 0
    # le gain publié doit tomber dans la plage observée, à l'ordre de grandeur
    # près : les autres graines tournent à moins d'itérations.
    gain = export["simulation"]["modes"]["anti"]["gain"]
    assert gain <= 3 * max(d["max"], 1.0), (
        f"le gain publié ({gain} €) est hors de toute plage plausible "
        f"(max observé {d['max']} €)")


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


def test_l_historique_complet_est_exporte_tirage_par_tirage(export):
    """v2.7 — la page ne montre plus seulement les gains : chaque tirage
    rejoué a sa ligne, perdant compris, avec les vraies boules sorties.
    Le total misé/gagné doit se recomposer depuis ce détail complet."""
    sim = export["simulation"]
    a = sim["modes"]["anti"]
    cfg = JEUX[export["meta"]["jeu"]]
    lignes = a["tirages"]
    assert len(lignes) == sim["n_tirages"], (
        "l'historique complet doit couvrir TOUS les tirages simulés")
    assert lignes == sorted(lignes, key=lambda g: g["date"], reverse=True), (
        "tirages non triés du plus récent au plus ancien")
    total = 0.0
    for g in lignes:
        for cle in ("date", "grille", "bonus", "sortis", "bonus_sortis",
                    "bons", "bonus_ok", "rang", "gain"):
            assert cle in g, cle
        assert len(g["sortis"]) == cfg["pick"]
        assert len(g["bonus_sortis"]) == cfg["bonus_pick"]
        assert set(g["bons"]) == set(g["grille"]) & set(g["sortis"])
        if g["rang"] is None:
            assert g["gain"] == 0, "un gain sans rang est impossible"
        total += g["gain"]
    assert abs(total - a["gain"]) < 0.02, (
        "l'historique complet ne recompose pas le total gagné")
    assert sum(1 for g in lignes if g["rang"]) == a["n_gains"]


def test_chaque_grille_affichee_est_reglee_contre_le_dernier_tirage(export):
    """v2.7 — la page affiche chaque grille avec son résultat contre les
    vraies boules du dernier tirage FDJ : la grille du haut, les tickets
    multiples et le système."""
    dern = export["dernier_tirage"]
    grilles = list(export["modes"]["anti"]["grilles"])
    if export.get("systeme"):
        grilles += export["systeme"]["grilles"]
    for g in grilles:
        v = g.get("vs_dernier")
        assert v, "grille exportée sans règlement contre le dernier tirage"
        for cle in ("bons", "bonus_ok", "rang", "gain"):
            assert cle in v, cle
        assert set(v["bons"]) == set(g["numeros"]) & set(dern["numeros"])
        assert set(v["bonus_ok"]) == set(g["bonus"]) & set(dern["bonus"])
        if v["rang"] is None:
            assert v["gain"] == 0


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
        assert len({(x - 1) // 10 for x in b}) >= 2, f"{b} : moins de 2 dizaines"
        suite = 1
        for k in range(1, len(b)):
            suite = suite + 1 if b[k] == b[k - 1] + 1 else 1
            assert suite < 3, f"{b} : 3 numéros consécutifs"
