#!/usr/bin/env python3
"""TOURNOI DES MÉTHODES — on les fait toutes concourir, on classe, on tranche.

Toutes les méthodes qu'on trouve dans les études sur le loto et dans les dépôts
GitHub de prédiction se ramènent à la même chose : donner un SCORE à chaque
numéro depuis l'historique, puis jouer les mieux notés. Elles ne diffèrent que
par le score.

On les met donc toutes sur la même ligne de départ, dans les mêmes conditions :

  · WALK-FORWARD STRICT — à chaque tirage, la méthode ne voit que les tirages
    ANTÉRIEURS. Aucune ne peut tricher, même par accident.
  · MÊME MÉTRIQUE — combien de bons numéros dans son top-5, et combien d'euros
    elle aurait rapportés aux vrais rapports FDJ.
  · MÊME TÉMOIN — un tirage au sort, joué dans les mêmes conditions.

L'étalon à battre est connu d'avance : en jouant 5 numéros sur 49, on en trouve
en moyenne 5 × 5/49 = 0,5102 par tirage. Une méthode qui « marche » doit faire
significativement mieux QUE CE CHIFFRE, pas mieux qu'une autre méthode.

    python3 tools/tournoi_methodes.py [--jeu loto|euromillions] [--depart 300]
"""
from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "tests"))

from test_rangs import tirages_des_archives  # noqa: E402

import recherche  # noqa: E402
from oracle import JEUX, prix_du_tirage, rang_gagne  # noqa: E402

# ---------------------------------------------------------------------------
# Méthodes de GRILLE — celles qui ne se ramènent pas à un score par numéro
# ---------------------------------------------------------------------------

def _somme_cible(cfg, passe, k):
    """« Joue une grille dont la somme est proche de la moyenne historique. »
    Heuristique très répandue. On note chaque numéro par sa proximité à la
    moyenne par numéro, ce qui est la version scorable de la règle."""
    if not passe:
        return dict.fromkeys(range(1, cfg["n_max"] + 1), 0.0)
    moy = statistics.mean(sum(t["balls"]) for t in passe) / cfg["pick"]
    return {n: -abs(n - moy) for n in range(1, cfg["n_max"] + 1)}


def _cycles(cfg, passe, k):
    """« Chaque numéro a un cycle de sortie ; joue ceux dont le cycle arrive
    à terme. » Version mesurable : écart actuel moins écart moyen du numéro."""
    dernier: dict[int, int] = {}
    ecarts: dict[int, list[int]] = {}
    for i, t in enumerate(passe):
        for b in t["balls"]:
            if b in dernier:
                ecarts.setdefault(b, []).append(i - dernier[b])
            dernier[b] = i
    n = len(passe)
    out = {}
    for x in range(1, cfg["n_max"] + 1):
        moy = statistics.mean(ecarts[x]) if ecarts.get(x) else cfg["n_max"] / cfg["pick"]
        depuis = n - dernier[x] if x in dernier else n
        out[x] = depuis - moy
    return out


METHODES_GRILLE = [
    ("somme_cible", _somme_cible),
    ("cycles_de_sortie", _cycles),
]


def evaluer(cle: str, depart: int, graine: int = 0):
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    pick, n_max = cfg["pick"], cfg["n_max"]
    theorique = pick * pick / n_max

    # Les 14 traits de recherche.py, calculés en WALK-FORWARD par construction :
    # rien de ce qui entre dans X[t] ne provient du tirage t ou d'après.
    X, cibles = recherche.construire(cfg, tir, depart=depart)
    testes = tir[depart:]
    assert len(X) == len(cibles) == len(testes)

    # Chaque trait devient DEUX méthodes : le suivre (chaud) ou l'inverser
    # (froid). C'est exactement le débat « numéros chauds contre numéros en
    # retard » qui structure toute la littérature grand public.
    methodes: dict[str, list[list[float]]] = {}
    for i, nom in enumerate(recherche.TRAITS):
        methodes[f"{nom} (fort)"] = [x[i] for x in X]
        methodes[f"{nom} (faible)"] = [[-v for v in x[i]] for x in X]

    # Les méthodes de grille, recalculées pas à pas sur le seul passé
    for nom, fn in METHODES_GRILLE:
        serie = []
        for j in range(len(testes)):
            sc = fn(cfg, tir[:depart + j], pick)
            serie.append([sc[n] for n in range(1, n_max + 1)])
        methodes[nom] = serie

    rng = random.Random(graine)
    resultats = []
    for nom, serie in methodes.items():
        bons, euros, mise = [], 0.0, 0.0
        for j, scores in enumerate(serie):
            top = sorted(range(1, n_max + 1), key=lambda n: -scores[n - 1])[:pick]
            reel = cibles[j]
            b = len(set(top) & reel)
            bons.append(b)
            t = testes[j]
            rang = rang_gagne(cfg, b, 0)
            if rang is not None:
                euros += t["rapports"].get(rang, 0.0) or 0.0
            mise += prix_du_tirage(cfg, t["date"])
        resultats.append({
            "nom": nom, "moyenne": statistics.mean(bons),
            "et": statistics.pstdev(bons) / math.sqrt(len(bons)),
            "euros": euros, "mise": mise,
        })

    # Témoin : tirage au sort, mêmes conditions, plusieurs répétitions
    hasard = []
    for _ in range(30):
        bons = []
        for j in range(len(testes)):
            top = rng.sample(range(1, n_max + 1), pick)
            bons.append(len(set(top) & cibles[j]))
        hasard.append(statistics.mean(bons))

    return {
        "cfg": cfg, "n_tests": len(testes), "theorique": theorique,
        "resultats": resultats,
        "hasard_moy": statistics.mean(hasard),
        "hasard_et": statistics.pstdev(hasard),
    }


def afficher(r):
    cfg, n = r["cfg"], r["n_tests"]
    theo = r["theorique"]
    res = sorted(r["resultats"], key=lambda x: -x["moyenne"])
    print(f"\n{'='*84}")
    print(f"{cfg['nom']} — {len(res)} méthodes, {n} tirages jugés en "
          f"walk-forward strict")
    print(f"{'='*84}")
    print(f"\nÉtalon à battre : {theo:.4f} bons numéros par tirage "
          f"(ce que donne n'importe quelle grille fixe)")
    print(f"Témoin tirage au sort : {r['hasard_moy']:.4f} "
          f"(± {r['hasard_et']:.4f} sur 30 répétitions)\n")

    # seuil de significativité, corrigé du nombre de méthodes testées
    seuil_t = 3.2          # ~ Bonferroni pour ~30 méthodes à 5 %
    print(f"{'#':>3} {'méthode':28} {'bons/tirage':>12} {'écart au':>10} "
          f"{'t':>7}  {'€ gagnés':>10} {'ROI':>8}")
    print(f"{'':>3} {'':28} {'':>12} {'théorique':>10} {'':>7}  {'':>10} {'':>8}")
    print("-" * 84)
    for i, m in enumerate(res, 1):
        ecart = m["moyenne"] - theo
        t = ecart / m["et"] if m["et"] else 0.0
        marque = " <<<" if t > seuil_t else ""
        roi = 100 * (m["euros"] - m["mise"]) / m["mise"]
        print(f"{i:>3} {m['nom']:28} {m['moyenne']:>12.4f} {ecart:>+10.4f} "
              f"{t:>+7.2f}  {m['euros']:>9.0f} € {roi:>7.1f}%{marque}")

    print("-" * 84)
    gagnantes = [m for m in res
                 if (m["moyenne"] - theo) / (m["et"] or 1) > seuil_t]
    print(f"\nMÉTHODES QUI BATTENT L'ÉTALON de façon significative "
          f"(|t| > {seuil_t}, correction pour {len(res)} méthodes testées) : "
          f"{len(gagnantes)}")
    if gagnantes:
        for m in gagnantes:
            print(f"   >>> {m['nom']} : {m['moyenne']:.4f} contre {theo:.4f}")
    else:
        meilleure = res[0]
        t = (meilleure["moyenne"] - theo) / (meilleure["et"] or 1)
        print("   AUCUNE.")
        print(f"   La meilleure du classement, « {meilleure['nom']} », "
              f"est à {t:+.2f} écart-type de l'étalon —")
        print(f"   c'est-à-dire dans le bruit. Sur {len(res)} méthodes "
              f"testées, on ATTEND une première place")
        print("   à environ +2 écarts-types par pur hasard.")

    perdantes = sum(1 for m in res if m["euros"] < m["mise"])
    print(f"\nEn euros : {perdantes}/{len(res)} méthodes perdent de l'argent. "
          f"La moins mauvaise rend "
          f"{max(100*(m['euros']-m['mise'])/m['mise'] for m in res):.1f} %.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jeu", choices=["loto", "euromillions", "tous"],
                    default="tous")
    ap.add_argument("--depart", type=int, default=300,
                    help="tirages réservés à l'amorçage des indicateurs")
    args = ap.parse_args()
    jeux = ["loto", "euromillions"] if args.jeu == "tous" else [args.jeu]
    for cle in jeux:
        afficher(evaluer(cle, args.depart))
    print(f"\n{'='*84}")
    print("""COMMENT LIRE CE TABLEAU

La colonne qui compte est « écart au théorique », pas le classement. Trente
méthodes produisent forcément un premier et un dernier : ce qui distingue un
vrai signal d'un classement de bruit, c'est l'AMPLEUR de l'écart rapportée à
son incertitude — la colonne t.

Avec 30 méthodes testées, la première atteint +2 écarts-types par pur hasard.
Il faut donc dépasser ~3,2 pour conclure quoi que ce soit.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
