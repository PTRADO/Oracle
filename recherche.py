#!/usr/bin/env python3
"""
================================================================================
 ORACLE — RECHERCHE DE FORMULE (le « méga-algo »)
================================================================================

Objet : chercher, aussi loin que possible, une combinaison de méthodes qui
prédirait mieux que le hasard le tirage suivant — et surtout, DIRE SI CE QU'ON
TROUVE EST RÉEL.

Pourquoi ce second point est le cœur du module
----------------------------------------------
Avec 49 numéros, 14 indicateurs et des milliers de jeux de poids, on trouvera
TOUJOURS une formule qui aurait brillé sur le passé. C'est arithmétique : on
tire des milliers de tickets dans une loterie de formules, l'un d'eux gagne.
Le publier comme « LA formule » serait le mensonge central de ce produit.

Trois garde-fous sont donc câblés dans le protocole :

  1. VALIDATION HORS ÉCHANTILLON — la formule est cherchée sur les premiers
     70 % des tirages, puis jugée UNIQUEMENT sur les 30 % suivants, qu'elle
     n'a jamais vus. Une formule qui mémorise le bruit s'effondre ici.

  2. MODÈLE NUL PAR PERMUTATION — la recherche entière est relancée sur les
     mêmes tirages dont on a mélangé l'ORDRE. Toute structure temporelle est
     détruite ; il ne reste que du hasard. Si la meilleure formule des vraies
     données ne bat pas la meilleure formule du bruit, l'écart observé n'est
     que la trace de la recherche elle-même.

  3. VALIDATION DU CHERCHEUR — sur des tirages truqués où l'on a planté un
     biais connu, le chercheur DOIT le retrouver (test dans tests/). Sans
     cette preuve, un résultat négatif ne vaudrait rien : il pourrait juste
     signifier « mon chercheur est aveugle ».

Tout est en stdlib pure, comme le reste du moteur.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict, deque

# ==============================================================================
# 1. BIBLIOTHÈQUE DE TRAITS
# ==============================================================================
# Chaque trait attribue un nombre à chaque numéro, calculé UNIQUEMENT à partir
# des tirages passés. Les familles T1-T8 du moteur sont reprises, déclinées sur
# plusieurs fenêtres (un « chaud sur 50 tirages » n'est pas un « chaud sur
# 250 »), et complétées par des idées que le moteur n'exploitait pas.

TRAITS = [
    "freq_tout",      # T1 sur tout l'historique
    "freq_50",        # T1 fenêtre courte
    "freq_250",       # T1 fenêtre longue
    "retard",         # T2 — tirages depuis la dernière sortie
    "retard_rel",     # T2 normalisé par l'écart d'attente théorique
    "ewma_10",        # T3 demi-vie courte
    "ewma_30",        # T3 demi-vie moyenne
    "ewma_100",       # T3 demi-vie longue
    "momentum_20",    # T4 — z-score de forme récente
    "markov",         # T5 — transition depuis le tirage précédent
    "paires",         # T6 — affinité avec les numéros du dernier tirage
    "jour",           # T7 — fréquence le jour de tirage visé
    "voisins",        # nouveau — n-1 / n+1 sortis récemment
    "tendance",       # nouveau — fréquence récente moins fréquence de fond
]

N_TRAITS = len(TRAITS)


def _zscore(valeurs: list[float]) -> list[float]:
    """Centre-réduit un trait sur les numéros d'un tirage donné.

    Indispensable : sans cela, un trait aux grandes valeurs (fréquence totale)
    écraserait mécaniquement un trait aux petites (momentum), et les poids ne
    seraient plus comparables entre eux.
    """
    moy = sum(valeurs) / len(valeurs)
    var = sum((v - moy) ** 2 for v in valeurs) / len(valeurs)
    ecart = math.sqrt(var)
    if ecart < 1e-12:
        return [0.0] * len(valeurs)
    return [(v - moy) / ecart for v in valeurs]


class _Etat:
    """Accumulateurs mis à jour tirage après tirage.

    Tout est incrémental : recalculer chaque trait depuis le début à chaque
    tirage coûterait un facteur ~1000 et rendrait la recherche impraticable.
    """

    def __init__(self, n_max: int, pick: int):
        self.n_max, self.pick = n_max, pick
        self.N = list(range(1, n_max + 1))
        self.i = 0
        self.freq = Counter()
        self.dernier = dict.fromkeys(self.N, -1)
        self.f50, self.f250 = Counter(), Counter()
        self.q50, self.q250 = deque(), deque()
        self.ewma = {h: dict.fromkeys(self.N, 0.0) for h in (10, 30, 100)}
        self.fen20 = deque()
        self.markov = defaultdict(Counter)
        self.paires = Counter()
        self.par_jour = defaultdict(Counter)
        self.precedent: tuple[int, ...] = ()
        self.recents: deque[tuple[int, ...]] = deque()

    def traits(self, jour_vise: int) -> list[list[float]]:
        """Rend N_TRAITS listes de n_max valeurs, déjà centrées-réduites."""
        n_max, N, i = self.n_max, self.N, self.i
        sorties = []

        sorties.append([float(self.freq.get(n, 0)) for n in N])
        sorties.append([float(self.f50.get(n, 0)) for n in N])
        sorties.append([float(self.f250.get(n, 0)) for n in N])

        retard = [float(i - self.dernier[n]) if self.dernier[n] >= 0 else float(i + 1)
                  for n in N]
        sorties.append(retard)
        attente = n_max / self.pick          # écart moyen entre deux sorties
        sorties.append([r / attente for r in retard])

        for h in (10, 30, 100):
            e = self.ewma[h]
            sorties.append([e[n] for n in N])

        c20 = Counter()
        for t in self.fen20:
            c20.update(t)
        m = len(self.fen20)
        p = self.pick / n_max
        attendu = m * p
        et = math.sqrt(m * p * (1 - p)) or 1.0
        sorties.append([(c20.get(n, 0) - attendu) / et for n in N])

        mk = [0.0] * n_max
        for b in self.precedent:
            ligne = self.markov[b]
            total = sum(ligne.values()) or 1
            for j, n in enumerate(N):
                mk[j] += ligne.get(n, 0) / total
        sorties.append(mk)

        pr = [0.0] * n_max
        for b in self.precedent:
            for j, n in enumerate(N):
                if n != b:
                    pr[j] += self.paires.get((min(b, n), max(b, n)), 0)
        sorties.append(pr)

        cj = self.par_jour[jour_vise]
        sorties.append([float(cj.get(n, 0)) for n in N])

        vus = set()
        for t in self.recents:
            vus.update(t)
        sorties.append([float((n - 1 in vus) + (n + 1 in vus)) for n in N])

        base = max(i, 1)
        sorties.append([self.f50.get(n, 0) / max(len(self.q50), 1)
                        - self.freq.get(n, 0) / base for n in N])

        return [_zscore(s) for s in sorties]

    def absorber(self, tirage: dict) -> None:
        boules = tirage["balls"]
        self.q50.append(boules)
        self.f50.update(boules)
        if len(self.q50) > 50:
            self.f50.subtract(self.q50.popleft())
        self.q250.append(boules)
        self.f250.update(boules)
        if len(self.q250) > 250:
            self.f250.subtract(self.q250.popleft())

        for h in (10, 30, 100):
            decay = 0.5 ** (1 / h)
            e = self.ewma[h]
            for n in self.N:
                e[n] *= decay
            for b in boules:
                e[b] += 1.0

        for b in boules:
            self.freq[b] += 1
            self.dernier[b] = self.i
        self.par_jour[tirage["jour"]].update(boules)

        if self.precedent:
            for a in self.precedent:
                self.markov[a].update(boules)
        for x in range(len(boules)):
            for y in range(x + 1, len(boules)):
                self.paires[(boules[x], boules[y])] += 1

        self.fen20.append(boules)
        if len(self.fen20) > 20:
            self.fen20.popleft()
        self.recents.append(boules)
        if len(self.recents) > 3:
            self.recents.popleft()

        self.precedent = boules
        self.i += 1


def construire(cfg, tirages: list[dict], depart: int = 60):
    """Prépare la matrice des traits, en aveugle.

    Rend (X, cibles) où X[t] est une liste de N_TRAITS listes de n_max valeurs
    pour le t-ième tirage évalué, et cibles[t] l'ensemble des numéros réellement
    sortis. Rien de ce qui entre dans X[t] ne provient du tirage t ou d'après :
    c'est la condition pour que la mesure ait un sens.
    """
    etat = _Etat(cfg["n_max"], cfg["pick"])
    X, cibles = [], []
    for idx, t in enumerate(tirages):
        if idx >= depart:
            X.append(etat.traits(t["jour"]))
            cibles.append(set(t["balls"]))
        etat.absorber(t)
    return X, cibles


# ==============================================================================
# 2. ÉVALUATION D'UNE FORMULE
# ==============================================================================

def evaluer(X, cibles, poids: list[float], pick: int,
            debut: int = 0, fin: int | None = None) -> float:
    """Nombre moyen de bons numéros obtenus en jouant le top-`pick`.

    C'est la métrique que l'utilisateur a demandée : « qui s'approche le plus
    du tirage parfait ». Espérance sous hasard pur : pick² / n_max.
    """
    fin = len(X) if fin is None else fin
    if fin <= debut:
        return 0.0
    total = 0
    for t in range(debut, fin):
        traits = X[t]
        n_max = len(traits[0])
        score = [0.0] * n_max
        for w, colonne in zip(poids, traits, strict=True):
            if w:
                for j in range(n_max):
                    score[j] += w * colonne[j]
        meilleurs = sorted(range(n_max), key=lambda j: -score[j])[:pick]
        reel = cibles[t]
        total += sum(1 for j in meilleurs if j + 1 in reel)
    return total / (fin - debut)


# ==============================================================================
# 3. LE CHERCHEUR
# ==============================================================================

def _poids_hasard(rng: random.Random) -> list[float]:
    v = [rng.gauss(0, 1) for _ in range(N_TRAITS)]
    norme = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norme for x in v]


def _ascension(X, cibles, pick, depart, score_depart, coupure, tours=4):
    """Ascension de coordonnées : on pousse chaque poids tant que ça monte."""
    meilleur, score = list(depart), score_depart
    pas = 0.5
    for _ in range(tours):
        ameliore = False
        for f in range(N_TRAITS):
            for delta in (pas, -pas):
                essai = list(meilleur)
                essai[f] += delta
                s = evaluer(X, cibles, essai, pick, 0, coupure)
                if s > score:
                    meilleur, score = essai, s
                    ameliore = True
        if not ameliore:
            pas /= 2
    return meilleur, score


def chercher(X, cibles, pick: int, rng: random.Random,
             budget: int = 400, part_entrainement: float = 0.7,
             affiner: bool = True, departs: int = 5):
    """Cherche la meilleure formule sur la partie ENTRAÎNEMENT.

    Trois temps : balayage aléatoire large, puis ascension de coordonnées
    depuis les `departs` meilleurs points (et non le seul premier — sans quoi
    la recherche sature immédiatement dans le même optimum local, quel que
    soit le budget), puis on garde le meilleur.

    La partie VALIDATION n'est jamais touchée ici : c'est ce qui rend le
    verdict lisible.
    """
    coupure = int(len(X) * part_entrainement)

    # Les traits seuls font partie des candidats : si l'un d'eux suffisait,
    # il ne faudrait pas le rater au profit d'une combinaison alambiquée.
    candidats = []
    for f in range(N_TRAITS):
        for signe in (1.0, -1.0):
            v = [0.0] * N_TRAITS
            v[f] = signe
            candidats.append(v)
    candidats += [_poids_hasard(rng) for _ in range(max(0, budget - len(candidats)))]

    notes = [(evaluer(X, cibles, p, pick, 0, coupure), i)
             for i, p in enumerate(candidats)]
    notes.sort(reverse=True)

    meilleur, score_meilleur = list(candidats[notes[0][1]]), notes[0][0]
    if affiner:
        for score_dep, idx in notes[:max(1, departs)]:
            p, s = _ascension(X, cibles, pick, candidats[idx], score_dep, coupure)
            if s > score_meilleur:
                meilleur, score_meilleur = p, s

    return {
        "poids": meilleur,
        "score_entrainement": score_meilleur,
        "score_validation": evaluer(X, cibles, meilleur, pick, coupure, len(X)),
        "n_entrainement": coupure,
        "n_validation": len(X) - coupure,
        "budget": len(candidats),
        "departs_affines": max(1, departs) if affiner else 0,
    }


# ==============================================================================
# 4. MODÈLE NUL — la même recherche sur du bruit
# ==============================================================================

def permuter(tirages: list[dict], rng: random.Random) -> list[dict]:
    """Mélange l'ORDRE des tirages en gardant les dates et les jours.

    Chaque tirage reste un vrai tirage FDJ ; seule la chronologie est détruite.
    Toute « mémoire » que les traits croiraient lire devient donc illusoire par
    construction. C'est le témoin idéal : même distribution, zéro structure.
    """
    boules = [t["balls"] for t in tirages]
    rng.shuffle(boules)
    return [{**t, "balls": b} for t, b in zip(tirages, boules, strict=True)]


def etude_complete(cfg, tirages: list[dict], rng: random.Random,
                   budget: int = 400, n_nuls: int = 20, depart: int = 60,
                   departs: int = 5):
    """Protocole complet : recherche réelle, puis N recherches témoins.

    Le verdict ne compare pas la formule au hasard — il compare la MEILLEURE
    formule trouvée sur les vraies données à la MEILLEURE formule trouvée sur
    des données sans structure, à effort de recherche identique. C'est la seule
    comparaison honnête quand on a le droit d'essayer des milliers de formules.
    """
    X, cibles = construire(cfg, tirages, depart)
    if len(X) < 200:
        return None
    pick = cfg["n_max"], cfg["pick"]
    theorique = cfg["pick"] ** 2 / cfg["n_max"]

    reel = chercher(X, cibles, cfg["pick"], rng, budget, departs=departs)

    nuls = []
    for _ in range(n_nuls):
        Xp, cp = construire(cfg, permuter(tirages, rng), depart)
        nuls.append(chercher(Xp, cp, cfg["pick"], rng, budget,
                             departs=departs))

    vals = [n["score_validation"] for n in nuls]
    moy_nul = statistics.mean(vals)
    et_nul = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    z = ((reel["score_validation"] - moy_nul) / et_nul) if et_nul > 1e-9 else 0.0

    # p-valeur empirique — plus honnête que le z quand les témoins sont peu
    # nombreux : elle compte simplement combien de recherches sur du bruit ont
    # fait aussi bien que la recherche sur les vraies données. Le « +1 » au
    # numérateur évite d'annoncer p = 0, qu'aucun échantillon fini ne permet.
    au_moins_aussi_bien = sum(1 for v in vals if v >= reel["score_validation"])
    p_emp = (1 + au_moins_aussi_bien) / (1 + len(vals))

    del pick
    return {
        "n_tirages_evalues": len(X),
        "theorique": round(theorique, 4),
        "reel": {
            "score_entrainement": round(reel["score_entrainement"], 4),
            "score_validation": round(reel["score_validation"], 4),
            "n_entrainement": reel["n_entrainement"],
            "n_validation": reel["n_validation"],
            "poids": {TRAITS[i]: round(w, 3)
                      for i, w in enumerate(reel["poids"]) if abs(w) > 0.05},
        },
        "nul": {
            "n_essais": n_nuls,
            "score_validation_moyen": round(moy_nul, 4),
            "score_validation_max": round(max(vals), 4),
            "ecart_type": round(et_nul, 4),
            "entrainement_moyen": round(
                statistics.mean(n["score_entrainement"] for n in nuls), 4),
            "au_moins_aussi_bien": au_moins_aussi_bien,
        },
        "budget_par_recherche": reel["budget"],
        "z_vs_nul": round(z, 2),
        "p_empirique": round(p_emp, 3),
        "verdict": _verdict(reel, z, p_emp, theorique, len(vals)),
    }


def _verdict(reel, z, p_emp, theorique, n_nuls) -> str:
    """Le verdict s'appuie sur la p-valeur empirique, pas sur le z.

    Avec une poignée de témoins, l'écart-type est lui-même très incertain :
    un z de 2 peut n'être qu'un artefact d'estimation. Compter combien de
    recherches sur du bruit ont fait aussi bien est plus robuste, et ne
    suppose rien sur la forme de la distribution.
    """
    perte = reel["score_entrainement"] - reel["score_validation"]
    seuil = 1 / (1 + n_nuls)                 # p minimale atteignable
    if p_emp <= seuil + 1e-9 and z >= 3:
        return ("Aucun témoin n'a fait aussi bien, et l'écart dépasse 3 "
                "écarts-types. Avant toute autre conclusion : vérifier "
                "l'intégrité des données et reproduire sur une autre période. "
                "Un vrai signal dans une loterie serait un défaut matériel, "
                "pas une régularité mathématique.")
    if p_emp <= 0.05:
        return (f"Résultat limite (p = {p_emp:.3f} sur {n_nuls} témoins) : "
                "intrigant, mais très loin de suffire. À reproduire sur "
                "d'autres périodes avant d'en tirer quoi que ce soit.")
    return (f"Aucun signal. La meilleure formule perd {perte:+.3f} bon numéro "
            f"entre entraînement et validation, et {p_emp:.0%} des recherches "
            f"menées sur des données sans structure ont fait aussi bien. "
            f"L'écart d'entraînement est le produit de la recherche "
            f"elle-même, pas d'une régularité des tirages. "
            f"Espérance du hasard : {theorique:.4f}.")
