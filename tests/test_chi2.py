"""Le χ² d'équiprobabilité : on teste l'instrument, pas seulement le verdict.

`test_chi2` est le seul détecteur d'« edge » que le produit revendique. Un
détecteur dont on ignore le taux de fausse alarme et la puissance ne vaut
rien : son « boules équilibrées » ne voudrait rien dire. Ces tests
établissent donc deux choses par simulation, sans rien croire sur parole :

  1. sa loi sous H0 — un tirage prend `pick` boules SANS REMISE, ce qui
     n'est pas une multinomiale : E[χ²] = n_max − pick, pas n_max − 1 ;
  2. sa puissance — il doit voir une urne réellement biaisée.
"""
from __future__ import annotations

import random

import pytest

from oracle import JEUX
from oracle import test_chi2 as chi2_moteur

TOUS = [JEUX["loto"], JEUX["euromillions"]]
IDS = ["loto", "euromillions"]
N_TIRAGES = 1200
N_REPETITIONS = 300


def _tirages(cfg, generateur, n, rng):
    from datetime import date, timedelta
    d0 = date(2017, 1, 3)
    return [{"date": d0 + timedelta(days=3 * i), "jour": 0,
             "balls": generateur(rng), "bonus": [1],
             "rapports": {}, "gagnants": {}}
            for i in range(n)]


def _equitable(cfg):
    return lambda rng: sorted(rng.sample(range(1, cfg["n_max"] + 1),
                                         cfg["pick"]))


def _biaise(cfg, force):
    """Urne truquée : les 10 premières boules sortent `force` fois plus."""
    def tirer(rng):
        pool = list(range(1, cfg["n_max"] + 1))
        poids = [force if n <= 10 else 1.0 for n in pool]
        out = []
        for _ in range(cfg["pick"]):
            seuil = rng.uniform(0, sum(poids))
            acc = 0.0
            choisi = len(poids) - 1
            for rang, poids_n in enumerate(poids):
                acc += poids_n
                if acc >= seuil:
                    choisi = rang
                    break
            out.append(pool.pop(choisi))
            poids.pop(choisi)
        return sorted(out)
    return tirer


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_esperance_du_chi2_sous_h0_vaut_nmax_moins_pick(cfg):
    """Sous H0, E[χ²] = n_max − pick.

    Chaque boule apparaît dans un tirage avec p = pick/n_max, indépendamment
    d'un tirage à l'autre : Var(O_i) = n·p(1−p) et E[(O_i−E)²/E] = 1−p.
    Sommé sur les n_max boules : E[χ²] = n_max(1−p) = n_max − pick.
    Une multinomiale donnerait n_max − 1 — c'est ce que suppose `ddl`.
    """
    rng = random.Random(20260805)
    vals = [chi2_moteur(cfg, _tirages(cfg, _equitable(cfg), N_TIRAGES, rng))["chi2"]
            for _ in range(N_REPETITIONS)]
    moyenne = sum(vals) / len(vals)
    attendu = cfg["n_max"] - cfg["pick"]
    assert moyenne == pytest.approx(attendu, rel=0.03), (
        f"E[χ²] mesuré {moyenne:.2f}, attendu {attendu} "
        f"(sans remise) et non {cfg['n_max'] - 1} (multinomiale)")


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_le_seuil_5pct_donne_bien_5pct_de_fausses_alarmes(cfg):
    """Un « seuil 5 % » qui déclenche à 2 % n'est pas un seuil 5 %.

    Trop conservateur, le test rate de vrais biais tout en affirmant
    « boules équilibrées » — il transforme son manque de sensibilité en
    certitude. On exige que le taux réel encadre les 5 % annoncés.
    """
    rng = random.Random(20260806)
    vals = [chi2_moteur(cfg, _tirages(cfg, _equitable(cfg), N_TIRAGES, rng))["chi2"]
            for _ in range(N_REPETITIONS)]
    seuil = chi2_moteur(cfg, _tirages(cfg, _equitable(cfg), 200, rng))["seuil_5pct"]
    taux = sum(1 for v in vals if v > seuil) / len(vals)
    assert 0.03 <= taux <= 0.09, (
        f"taux de fausse alarme réel {taux:.1%} pour un seuil annoncé à 5 %")


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_le_chi2_voit_une_urne_franchement_biaisee(cfg):
    """La puissance : sans elle, « edge fermé » ne prouve rien."""
    rng = random.Random(20260807)
    r = chi2_moteur(cfg, _tirages(cfg, _biaise(cfg, 1.5), N_TIRAGES, rng))
    assert r["biais_detecte"], (
        f"urne truquée ×1,5 non détectée (χ² {r['chi2']:.1f} "
        f"vs seuil {r['seuil_5pct']:.1f})")


@pytest.mark.parametrize("cfg", TOUS, ids=IDS)
def test_le_chi2_ne_crie_pas_au_biais_sur_une_urne_saine(cfg):
    rng = random.Random(20260808)
    r = chi2_moteur(cfg, _tirages(cfg, _equitable(cfg), N_TIRAGES, rng))
    assert not r["biais_detecte"]
