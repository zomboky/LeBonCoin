---
name: golddigger02
description: Cherche sur leboncoin et fait remonter les « pépites » — annonces sous-évaluées par prix ou par ignorance du vendeur (modèle non identifié, marque mal orthographiée, reproduction vs original). Peut aussi vérifier un modèle/marque suspect sur des pages web externes (research). Utiliser quand on demande de chercher/surveiller des annonces leboncoin, de trouver de bonnes affaires dans un domaine (aviation, espace, simulateur de vol, militaria, Michelin collector, technologie vintage, mode vintage, collector), ou de confirmer la rareté/valeur d'une référence identifiée.
---

# GoldDigger02

Outil CLI (`golddigger02/`, invoqué via `python3 -m golddigger02`). Toute la
logique lourde (fetch, cohortes, statistiques, extraction de marque/modèle,
scoring, extraction de pages web) est en Python — n'essaie jamais de reproduire
ce raisonnement en lisant du JSON ou du HTML brut : appelle l'outil et lis sa
sortie compacte.

## Commande principale

```
python3 -m golddigger02 deals --domain <pack> [options]      # lance tout un pack thématique
python3 -m golddigger02 deals "<requête>" [options]           # une recherche libre
python3 -m golddigger02 watch --domain <pack>                 # uniquement le nouveau depuis le dernier passage
```

Packs disponibles : `aviation`, `space`, `flight-sim`, `militaria`, `michelin`,
`tech-vintage`, `vintage-clothing`, `collector`. Lister avec
`python3 -m golddigger02 domains`, détailler un pack avec
`python3 -m golddigger02 domains <nom>`. Ajouter un domaine ne demande aucun
code : déposer un `.yml` dans `~/.config/golddigger02/domains/` (format
documenté dans `README.md`).

## Options utiles

`--where <ville|dept|région>` `--min-price` `--max-price` `--seller private|pro`
`--pages N` (pages par requête-source) `--top N` (défaut 20) `--min-score`
`--format tsv|jsonl|ids|vision` `--unseen` (exclure le déjà-vu) `--delay S`
(pause entre requêtes, désactivée par défaut — usage personnel raisonnable).

## Flux de travail recommandé

1. `deals --domain X --top 20 --format tsv` (ou `watch` pour ne voir que le
   nouveau). Sortie : une ligne par annonce, colonnes
   `score prix ref titre ville age id pourquoi`. Ne PAS demander plus que
   nécessaire — `--top 20` suffit dans l'immense majorité des cas.
2. Regarder la colonne `pourquoi` : elle explique le score (écart de prix,
   modèle caché, marque mal orthographiée, marqueurs d'origine, REPRO...).
3. **Si un modèle/référence identifié est obscur et que le score en dépend
   fortement** : `research <url1> <url2>...` (voir plus bas) pour vérifier sa
   rareté sur le web avant de faire confiance au score. Optionnel, à réserver
   aux cas ambigus — ne pas systématiser.
4. Pour les 2-5 annonces vraiment intéressantes seulement, relancer avec
   `--format vision --top N` : ça sort les URLs d'images et la description
   complète. C'est le seul moment où dépenser des tokens de vision — pour
   confirmer une référence sur une photo (plaque, cadran, étiquette).
5. Pour une annonce précise : `python3 -m golddigger02 show <id> --domain <pack>`.

## Commande `research` — vérification externe (ex-Mach2)

Sert à confirmer qu'une référence obscure remontée par `hidden_model` ou
`typo_brand` est réellement rare/recherchée, en lisant des pages web
ordinaires (forums de collectionneurs, résultats d'enchères, fiches
techniques) — jamais leboncoin lui-même, qui a son propre pipeline JSON.

```
WebSearch (outil Claude) → collecter 2-3 URLs pertinentes sur le modèle identifié
python3 -m golddigger02 research <url1> <url2>... --filter "nom du modèle" --max-chars 2000
```

Le contenu complet est écrit en fichiers `.md` (+ `manifest.json`) ; seul un
résumé compact (titre, nb de mots, chemin) remonte en console. Lire le
manifest, puis n'ouvrir QUE les fichiers utiles — ne jamais lire le HTML brut
d'une page. `--render` pour les sites qui affichent leur contenu en
JavaScript (nécessite Playwright).

## Ce que dit le score

Un score haut ne veut pas seulement dire « pas cher ». C'est souvent le vendeur
qui n'a pas identifié ce qu'il vend : `modèle absent du titre`, `marque mal
orthographiée`, `annonce bâclée`. Un score écrasé avec la mention `REPRO` veut
dire qu'un marqueur de reproduction a été détecté — l'annonce n'est presque
sûrement pas authentique, quel que soit son prix affiché.

## Limite réseau à connaître

leboncoin est protégé par DataDome. Depuis une IP de datacenter, `deals` est
bloqué (403) — c'est attendu, pas un bug de l'outil. `research` n'est PAS
concerné : ce sont des pages web ordinaires, sans DataDome.

Détails d'architecture, format des packs, mémoire des annonces vues :
voir `README.md`.
