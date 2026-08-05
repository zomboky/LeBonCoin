---
name: leboncoin-deals
description: Cherche sur leboncoin et fait remonter les « pépites » — annonces sous-évaluées par prix ou par ignorance du vendeur (modèle non identifié, marque mal orthographiée, reproduction vs original). Utiliser quand on demande de chercher/surveiller des annonces leboncoin ou de trouver de bonnes affaires dans un domaine (aviation, espace, simulateur de vol, militaria, Michelin collector, technologie vintage, mode vintage, collector).
---

# leboncoin-deals

Outil CLI (`lbc`, dans `lbc/`). Toute la logique lourde (fetch, cohortes,
statistiques, extraction de marque/modèle, scoring) est en Python — n'essaie
jamais de reproduire ce raisonnement en lisant du JSON brut : appelle l'outil et
lis sa sortie compacte.

## Commande principale

```
python3 -m lbc deals --domain <pack> [options]      # lance tout un pack thématique
python3 -m lbc deals "<requête>" [options]           # une recherche libre
python3 -m lbc watch --domain <pack>                 # uniquement le nouveau depuis le dernier passage
```

Packs disponibles : `aviation`, `space`, `flight-sim`, `militaria`, `michelin`,
`tech-vintage`, `vintage-clothing`, `collector`. Lister avec
`python3 -m lbc domains`, détailler un pack avec `python3 -m lbc domains <nom>`.
Ajouter un domaine ne demande aucun code : déposer un `.yml` dans
`~/.config/lbc/domains/` (format documenté dans `README.md`).

## Options utiles

`--where <ville|dept|région>` `--min-price` `--max-price` `--seller private|pro`
`--pages N` (pages par requête-source) `--top N` (défaut 20) `--min-score`
`--format tsv|jsonl|ids|vision` `--unseen` (exclure le déjà-vu) `--delay S`
(pause entre requêtes, désactivée par défaut — usage personnel raisonnable).

## Flux de travail recommandé

1. `deals --domain X --top 20 --format tsv` (ou `watch` pour ne voir que le
   nouveau). Sortie : une ligne par annonce, ~45 tokens/ligne en moyenne,
   colonnes `score prix ref titre ville age id pourquoi`. Ne PAS demander plus
   que nécessaire — `--top 20` suffit dans l'immense majorité des cas.
2. Regarder la colonne `pourquoi` : elle explique le score (écart de prix,
   modèle caché, marque mal orthographiée, marqueurs d'origine, REPRO...).
3. Pour les 2-5 annonces vraiment intéressantes seulement, relancer avec
   `--format vision --top N` : ça sort les URLs d'images et la description
   complète. C'est le seul moment où dépenser des tokens de vision — pour
   confirmer une référence sur une photo (plaque, cadran, étiquette).
4. Pour une annonce précise : `python3 -m lbc show <id> --domain <pack>`.

## Ce que dit le score

Un score haut ne veut pas seulement dire « pas cher ». C'est souvent le vendeur
qui n'a pas identifié ce qu'il vend : `modèle absent du titre`, `marque mal
orthographiée`, `annonce bâclée`. Un score écrasé avec la mention `REPRO` veut
dire qu'un marqueur de reproduction a été détecté — l'annonce n'est presque
sûrement pas authentique, quel que soit son prix affiché.

## Limite réseau à connaître

leboncoin est protégé par DataDome. Depuis une IP de datacenter, tout est
bloqué (403) — c'est attendu, pas un bug de l'outil. Il faut tourner depuis une
connexion résidentielle normale. Si `deals` renvoie une erreur réseau claire,
la relayer telle quelle plutôt que d'insister en boucle.

Détails d'architecture, format des packs, mémoire des annonces vues : voir
`README.md`.
