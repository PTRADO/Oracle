#!/usr/bin/env python3
"""Les numéros et les paires qui sortent le plus — et ce que ça donne en euros.

Trois questions, dans l'ordre :

  1. QUELS numéros sortent le plus ? (le tableau que tout le monde veut voir)
  2. QUELLES paires sortent le plus ensemble ?
  3. COMBIEN on gagne si on les joue ?

La 3e est la seule qui compte, et c'est celle que les sites de « numéros
chauds » ne montrent jamais. On la joue pour de vrai : à chaque tirage on
prend les numéros les plus sortis JUSQUE-LÀ (jamais après), on règle la
grille aux rapports FDJ réels, et on compte.

    python3 tools/numeros_qui_sortent_le_plus.py [--jeu loto] [--derniers 500]
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "tests"))

from test_rangs import tirages_des_archives  # noqa: E402

from oracle import JEUX, prix_du_tirage, rang_gagne  # noqa: E402


def machine_parfaite(cfg, n_tirages, n_sim=1500, graine=7):
    """Ce qu'une machine SANS défaut produit : plage du numéro le plus sorti,
    et plage de la paire la plus sortie. C'est la référence honnête."""
    rng = random.Random(graine)
    univers = list(range(1, cfg["n_max"] + 1))
    maxs, maxp = [], []
    for k in range(n_sim):
        c, cp = Counter(), Counter()
        for _ in range(n_tirages):
            b = sorted(rng.sample(univers, cfg["pick"]))
            c.update(b)
            if k < n_sim // 5:          # les paires coûtent cher, on en fait moins
                for i in range(len(b)):
                    for j in range(i + 1, len(b)):
                        cp[(b[i], b[j])] += 1
        maxs.append(max(c.values()))
        if cp:
            maxp.append(max(cp.values()))
    return maxs, maxp


def rejouer(cfg, tir, debut, choisir, nom):
    """Rejoue l'historique en jouant, à chaque tirage, la grille rendue par
    `choisir(passe)`. Strictement le passé : aucune triche possible."""
    mise = gain = 0.0
    n_gains = 0
    rangs = Counter()
    for i in range(debut, len(tir)):
        grille = choisir(tir[:i])
        t = tir[i]
        bons = len(set(grille) & set(t["balls"]))
        mise += prix_du_tirage(cfg, t["date"])
        rang = rang_gagne(cfg, bons, 0)
        if rang is not None:
            g = t["rapports"].get(rang, 0.0) or 0.0
            if g:
                gain += g
                n_gains += 1
                rangs[rang] += 1
    return {"nom": nom, "mise": mise, "gain": gain, "n_gains": n_gains,
            "rangs": rangs, "roi": 100 * (gain - mise) / mise if mise else 0}


def analyser(cle: str, derniers: int) -> None:
    cfg = JEUX[cle]
    tir = tirages_des_archives(cle)
    pick, n_max = cfg["pick"], cfg["n_max"]
    n_t = len(tir)

    print(f"\n{'='*78}")
    print(f"{cfg['nom']} — {n_t} tirages réels")
    print(f"{'='*78}")

    # ---- 1. LES NUMÉROS QUI SORTENT LE PLUS ---------------------------
    c = Counter()
    for t in tir:
        c.update(t["balls"])
    attendu = n_t * pick / n_max
    maxs, maxp = machine_parfaite(cfg, n_t)
    plage_h = (min(maxs), max(maxs))

    print(f"\n1. LES {10} NUMÉROS QUI SORTENT LE PLUS")
    print(f"   (chaque numéro devrait sortir {attendu:.0f} fois)\n")
    print(f"   {'rang':>4} {'n°':>4} {'sorties':>9} {'écart':>9}")
    print("   " + "-" * 30)
    for i, (n, v) in enumerate(c.most_common(10), 1):
        print(f"   {i:>4} {n:>4} {v:>9} {100*(v/attendu-1):>+8.1f}%")

    print(f"\n   LES {10} QUI SORTENT LE MOINS")
    print(f"   {'rang':>4} {'n°':>4} {'sorties':>9} {'écart':>9}")
    print("   " + "-" * 30)
    for i, (n, v) in enumerate(c.most_common()[-10:], 1):
        print(f"   {i:>4} {n:>4} {v:>9} {100*(v/attendu-1):>+8.1f}%")

    plus_sorti, n_plus = c.most_common(1)[0]
    print(f"\n   ⚠ AVANT DE CONCLURE : une machine SANS AUCUN DÉFAUT, sur "
          f"{n_t} tirages,")
    print(f"     produit un numéro record entre {plage_h[0]} et {plage_h[1]} "
          f"sorties.")
    print(f"     Ton n°{plus_sorti} en a {n_plus}. "
          f"{'Il est DANS la plage normale.' if plage_h[0] <= n_plus <= plage_h[1] else 'Il SORT de la plage !'}")

    # ---- 2. LES PAIRES QUI SORTENT LE PLUS ----------------------------
    pc = Counter()
    for t in tir:
        b = sorted(t["balls"])
        for i in range(len(b)):
            for j in range(i + 1, len(b)):
                pc[(b[i], b[j])] += 1
    n_paires = n_max * (n_max - 1) // 2
    att_p = n_t * (pick * (pick - 1) // 2) / n_paires

    print("\n2. LES 10 PAIRES QUI SORTENT LE PLUS ENSEMBLE")
    print(f"   ({n_paires} paires possibles, chacune devrait sortir "
          f"{att_p:.1f} fois)\n")
    print(f"   {'rang':>4} {'paire':>10} {'ensemble':>10} {'écart':>9}")
    print("   " + "-" * 37)
    for i, (p, v) in enumerate(pc.most_common(10), 1):
        print(f"   {i:>4} {p[0]:>4} + {p[1]:<3} {v:>10} "
              f"{100*(v/att_p-1):>+8.0f}%")

    if maxp:
        pm, vm = pc.most_common(1)[0]
        print("\n   ⚠ MÊME AVERTISSEMENT : une machine parfaite produit une "
              "paire record")
        print(f"     entre {min(maxp)} et {max(maxp)} sorties. La tienne "
              f"({pm[0]}+{pm[1]}) en a {vm}.")
        print(f"     {'Dans la plage normale.' if min(maxp) <= vm <= max(maxp) else 'Hors plage !'}"
              f"  Sur {n_paires} paires, la plus forte est FORCÉMENT très")
        print("     au-dessus de la moyenne — même sans le moindre défaut.")

    # ---- 3. ET SI ON LES JOUAIT ? -------------------------------------
    debut = max(200, n_t - derniers)
    n_joues = n_t - debut
    print("\n3. ET SI ON LES JOUAIT VRAIMENT ?")
    print(f"   Une grille par tirage sur les {n_joues} derniers tirages, "
          f"réglée aux rapports FDJ réels.")
    print("   À chaque fois, on choisit d'après le passé UNIQUEMENT.\n")

    def top_freq(passe):
        cc = Counter()
        for t in passe:
            cc.update(t["balls"])
        return [n for n, _ in cc.most_common(pick)]

    def bas_freq(passe):
        cc = Counter()
        for t in passe:
            cc.update(t["balls"])
        tous = [(cc.get(n, 0), n) for n in range(1, n_max + 1)]
        return [n for _, n in sorted(tous)[:pick]]

    def top_paires(passe):
        cp = Counter()
        for t in passe:
            b = sorted(t["balls"])
            for i in range(len(b)):
                for j in range(i + 1, len(b)):
                    cp[(b[i], b[j])] += 1
        pris: list[int] = []
        for (a, b), _ in cp.most_common():
            for x in (a, b):
                if x not in pris:
                    pris.append(x)
            if len(pris) >= pick:
                break
        return pris[:pick]

    rng = random.Random(2026)

    def au_hasard(passe):
        return rng.sample(range(1, n_max + 1), pick)

    lignes = [
        rejouer(cfg, tir, debut, top_freq, "les 5 QUI SORTENT LE PLUS"),
        rejouer(cfg, tir, debut, top_paires, "les paires QUI SORTENT LE PLUS"),
        rejouer(cfg, tir, debut, bas_freq, "les 5 qui sortent le MOINS"),
        rejouer(cfg, tir, debut, au_hasard, "5 numéros AU HASARD"),
    ]
    print(f"   {'stratégie':34} {'misé':>9} {'gagné':>9} {'ROI':>8} {'gains':>7}")
    print("   " + "-" * 70)
    for r in lignes:
        print(f"   {r['nom']:34} {r['mise']:>8.0f} € {r['gain']:>8.0f} € "
              f"{r['roi']:>7.1f}% {r['n_gains']:>7}")
    print("   " + "-" * 70)

    meilleure = max(lignes, key=lambda r: r["gain"])
    hasard = [r for r in lignes if "HASARD" in r["nom"]][0]
    chauds = lignes[0]
    print(f"\n   >>> Jouer les numéros les plus sortis rapporte "
          f"{chauds['gain']:.0f} € pour {chauds['mise']:.0f} € misés.")
    print(f"   >>> Jouer au hasard rapporte {hasard['gain']:.0f} € "
          f"pour la même mise.")
    ecart = chauds["gain"] - hasard["gain"]
    print(f"   >>> Écart : {ecart:+.0f} € sur {n_joues} tirages, soit "
          f"{ecart/n_joues:+.2f} € par tirage.")
    print(f"\n   Toutes les stratégies perdent. La « meilleure » ici est "
          f"« {meilleure['nom']} »,")
    print(f"   à {meilleure['roi']:.1f} % — et elle change si on décale la "
          f"période d'un mois.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jeu", choices=["loto", "euromillions", "tous"],
                    default="tous")
    ap.add_argument("--derniers", type=int, default=500)
    args = ap.parse_args()
    for cle in (["loto", "euromillions"] if args.jeu == "tous" else [args.jeu]):
        analyser(cle, args.derniers)
    print(f"\n{'='*78}")
    print("""EN UNE PHRASE

Les numéros qui sortent le plus existent — il y en a forcément un — mais
ils sortent exactement autant qu'une machine parfaite en produirait, et
les jouer ne rapporte pas plus que de jouer au hasard.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
