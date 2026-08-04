"""Tests du contrôle d'intégrité (`meta.alertes`).

Une anomalie de données doit être VUE, jamais absorbée en silence : c'est ce
que ces tests verrouillent.
"""
from __future__ import annotations

from datetime import date, timedelta

from conftest import fixture_texte

from oracle import JEUX, controle_integrite, parser_csv

LOTO = JEUX["loto"]
EURO = JEUX["euromillions"]
SOURCES_OK = [(a["label"], "?") for a in LOTO["archives"]]


def _tirages(nom="loto_201911", jeu="loto"):
    return parser_csv(JEUX[jeu], fixture_texte(nom))


def _messages(alertes, niveau=None):
    return " | ".join(a["message"] for a in alertes
                      if niveau is None or a["niveau"] == niveau)


def test_historique_sain_ne_leve_aucune_alerte():
    """Les 6 tirages de la fixture sont espacés (deux blocs distants) : on
    prend le bloc récent seul pour avoir un historique continu."""
    t = [x for x in _tirages() if x["date"].year == 2026]
    al = controle_integrite(LOTO, t, SOURCES_OK, t[-1]["date"] + timedelta(1))
    # volume faible attendu (fixture de 3 tirages), mais rien de critique
    assert not [a for a in al if a["niveau"] == "critique"], _messages(al)


def test_trou_superieur_a_7_jours_signale():
    t = _tirages()          # contient un trou 2019 → 2026
    al = controle_integrite(LOTO, t, SOURCES_OK, date(2026, 8, 4))
    assert "trou" in _messages(al, "attention").lower()


def test_epoque_manquante_signalee():
    t = _tirages()
    partiel = SOURCES_OK[:1]
    al = controle_integrite(LOTO, t, partiel, date(2026, 8, 4))
    msg = _messages(al, "attention")
    assert "Époque" in msg and LOTO["archives"][-1]["label"] in msg


def test_source_unique_ne_reproche_pas_les_epoques_absentes():
    """Avec --zip/--csv l'utilisateur impose sa source : on le signale une
    fois, sans lister comme « manquantes » des époques qu'il n'a pas
    demandées (et sans compter à tort celle qu'il a fournie)."""
    t = _tirages()
    al = controle_integrite(LOTO, t, [("loto_201911.zip", "?")],
                            date(2026, 8, 4), multi_epoques=False)
    msg = _messages(al, "attention")
    assert "Source unique imposée" in msg
    assert "non chargée" not in msg


def test_doublon_de_date_signale():
    t = _tirages()
    al = controle_integrite(LOTO, t, SOURCES_OK, date(2026, 8, 4),
                            doublons=[t[0]["date"]])
    assert "deux archives" in _messages(al, "attention")


def test_dates_non_croissantes_sont_critiques():
    t = _tirages()
    t = [t[2], t[0], t[1]]          # ordre cassé
    al = controle_integrite(LOTO, t, SOURCES_OK, date(2026, 8, 4))
    assert "croissantes" in _messages(al, "critique")


def test_tirage_special_detecte_par_son_jour():
    """Un Super Loto (vendredi/dimanche) mêlé au Loto doit être vu."""
    t = parser_csv(LOTO, fixture_texte("special_superloto"))
    al = controle_integrite(LOTO, t, SOURCES_OK, date(2026, 8, 4))
    assert "jour inhabituel" in _messages(al, "attention")


def test_historique_en_retard_signale():
    t = [x for x in _tirages() if x["date"].year == 2026]
    tard = t[-1]["date"] + timedelta(days=30)
    al = controle_integrite(LOTO, t, SOURCES_OK, tard)
    assert "en retard" in _messages(al, "attention")


def test_ancienne_formule_de_bonus_demasquee():
    """Une époque à 11 étoiles se parse sans erreur (11 ≤ 12) : seule
    l'absence totale de l'étoile 12 la trahit. On simule un historique
    volumineux en répliquant la fixture."""
    t = parser_csv(EURO, fixture_texte("legacy_euromillions_11_etoiles"))
    gros = []
    d = date(2017, 1, 3)
    for i in range(220):                       # > seuil de 200 tirages
        src = t[i % len(t)]
        gros.append({**src, "date": d, "jour": d.weekday()})
        d += timedelta(days=3 if d.weekday() == 1 else 4)
    al = controle_integrite(EURO, gros, [], gros[-1]["date"] + timedelta(1))
    msg = _messages(al, "critique")
    assert "jamais tiré" in msg and "12" in msg


def test_aucun_tirage_est_critique():
    al = controle_integrite(LOTO, [], SOURCES_OK, date(2026, 8, 4))
    assert al[-1]["niveau"] == "critique"


def test_toute_alerte_a_un_niveau_connu():
    t = _tirages()
    al = controle_integrite(LOTO, t, SOURCES_OK, date(2026, 8, 4))
    assert al
    for a in al:
        assert a["niveau"] in ("info", "attention", "critique")
        assert a["message"].strip()
