# lbc — recherche leboncoin & détection de pépites

Outil en ligne de commande, pensé pour être piloté par un agent aussi bien que
par un humain. Le principe : **tout le travail coûteux se fait en Python** —
récupération des annonces, regroupement en cohortes comparables, statistiques,
extraction de marque/modèle, scoring — pour que l'appelant (humain ou modèle)
ne reçoive qu'une short-list compacte, jamais du JSON brut.

## Installation

```bash
pip install -r requirements.txt
# Optionnel mais recommandé : repli navigateur si l'API interne est bloquée.
pip install playwright && playwright install chromium
```

Python 3.10+. Aucune base de données externe : SQLite est créé automatiquement
sous `~/.cache/lbc/`.

## Avertissement réseau : DataDome

leboncoin est protégé par DataDome. **Cet outil ne fonctionnera pas depuis une
IP de datacenter, un VPS ou un CI** — DataDome bloque ces plages avant même
d'atteindre le code (vérifié : 403 sur l'API, `ERR_CONNECTION_RESET` sur
Chromium via proxy). Il est fait pour tourner sur une machine personnelle avec
une connexion résidentielle normale.

Comportement en cas de blocage :
1. requête HTTP directe sur l'API interne (rapide, peu coûteux) ;
2. si 403 : ouverture ponctuelle de Chromium (Playwright) pour récolter un
   cookie `datadome` valide, puis retour en HTTP pour la suite ;
3. si l'API reste fermée : lecture du `__NEXT_DATA__` de la page de recherche.

Les cookies sont mis en cache (`~/.cache/lbc/state/cookies.json`) : un seul
passage navigateur suffit généralement pour toute une session.

L'usage prévu est personnel et raisonnable — pas de rate-limiting imposé
(option `--delay`, désactivée par défaut), mais pas de parallélisme agressif
ni de contournement de blocage au-delà d'un cookie légitime.

## Usage rapide

```bash
# Un pack thématique entier (lance toutes ses requêtes-sources)
python3 -m lbc deals --domain aviation --top 20

# Une recherche libre
python3 -m lbc deals "leica m6" --category telephonie --where 75 --max-price 800

# Uniquement les nouveautés depuis le dernier passage
python3 -m lbc watch --domain michelin

# Détail d'une annonce, avec images
python3 -m lbc show 123456789 --domain aviation
```

## Commandes

| Commande | Rôle |
|---|---|
| `deals` | chercher + classer par potentiel de pépite |
| `search` | recherche simple, sortie compacte, sans scoring |
| `watch` | comme `deals`, restreint aux annonces jamais vues |
| `show <id>` | détail d'une annonce, avec images |
| `domains [nom]` | lister ou inspecter les packs thématiques |
| `seen` | statistiques / purge de la mémoire des annonces vues |

Options principales de `deals`/`search`/`watch` : `--category` `--min-price`
`--max-price` `--where` `--radius` `--sort` `--seller private|pro` `--pages`
`--top` `--min-score` `--min-cohort` `--format tsv|jsonl|ids|vision` `--unseen`
`--delay` `--no-cache` `--no-browser` `--from-fixture <json>` (rejoue un JSON
local, utile pour tester sans réseau).

## Comment le score de pépite est calculé

Une pépite n'est pas seulement une annonce pas chère : le cas le plus payant
est celui où **le vendeur ignore ce qu'il vend**. Le score combine :

| Signal | Ce qu'il capte |
|---|---|
| `price_gap` | écart sous le prix de référence (cohorte ou barème du pack) |
| `hidden_model` | modèle identifié dans la description, absent du titre |
| `authenticity` | marqueurs d'origine trouvés ; un marqueur de reproduction écrase le score |
| `brand_tier` | pièce recherchée du pack vendue au prix du tout-venant |
| `typo_brand` | marque mal orthographiée → invisible dans les recherches normales |
| `weak_listing` | annonce bâclée (peu de photos, description courte) |
| `urgency` | vente pressée ou vendeur qui admet ne pas identifier l'objet |
| `private_seller` | un particulier price en général moins juste qu'un pro |
| `freshness` | annonce récente |

**Prix de référence.** Sur les annonces comparables (même catégorie, même
modèle extrait ou signature de titre), on prend la médiane si la cohorte compte
au moins `--min-cohort` membres (5 par défaut). En dessous — le cas fréquent
sur du collector rare — on retombe sur les `value_bands` du pack, des
fourchettes de prix curées par motif. La médiane et l'écart utilisent MAD
(écart absolu médian), robuste aux prix aberrants qui pullulent sur leboncoin.

**Garde-fous.** Un marqueur de reproduction détecté réduit le score à 15% de sa
valeur, quel que soit le prix. Un prix sous 5% de la référence est traité comme
suspect (erreur de saisie, pièce détachée, arnaque) plutôt que comme une
affaire.

## Packs thématiques

Un pack (`lbc/domains/*.yml`) décrit un terrain de chasse : catégories API,
requêtes-sources, marques, règles de détection de modèle, fautes
d'orthographe connues, marqueurs d'origine/reproduction, et barèmes de prix.

Livrés : `aviation`, `space` (conquête spatiale), `flight-sim` (matériel de
simulateur de vol), `militaria`, `michelin` (Michelin collector), `tech-vintage`
(informatique/électronique ancienne rare), `vintage-clothing`, `collector`
(générique).

**Ajouter un domaine ne demande aucun code** : déposer un fichier dans
`~/.config/lbc/domains/mon-pack.yml` (variable `LBC_DOMAIN_DIR` pour changer
l'emplacement). Un pack qui porte le nom d'un pack livré le remplace.

```yaml
name: mon-pack
label: Description humaine
categories: [collection, decoration]      # noms ou ids leboncoin (lbc/taxonomy.py)
queries:                                   # requêtes lancées par `deals --domain mon-pack`
  - terme de recherche 1
brands: [Marque A, Marque B]
models:
  - {pattern: "regex\\s*insensible\\s*a\\s*la\\s*casse", name: "Nom canonique"}
typos: {orthographe_fautive: Orthographe correcte}
origin_markers: [certificat, numero de serie]
repro_markers: [reproduction, réplique]
premium: [mot qui signale une pièce recherchée]
value_bands:
  - {match: "motif regex", low: 50, high: 300}   # référence = low + (high-low)*0.35
```

Voir `lbc/domains/aviation.yml` pour un exemple complet.

## Mémoire des annonces vues

SQLite (`~/.cache/lbc/cache.sqlite3`), deux tables distinctes :
- `responses` : cache des réponses HTTP, TTL configurable (`LBC_CACHE_TTL`,
  6h par défaut) — évite de refetch la même page ;
- `seen` : chaque annonce déjà affichée par `deals`/`watch`. `watch` (alias de
  `deals --unseen`) ne renvoie que ce qui n'y figure pas encore, souvent zéro
  ligne au bout de quelques passages.

`lbc seen` affiche les statistiques par pack. `lbc seen --forget <id...>` ou
`--forget-domain <pack>` ou `--all` réarment la mémoire.

## Budget de tokens (mesuré, pas estimé)

Sur une fixture de 500 annonces, `deals --top 20 --format tsv` produit environ
**900-950 tokens** de sortie (une ligne par annonce, colonnes + justification
courte), contre plusieurs centaines de milliers pour le JSON brut équivalent
— un facteur ~20-50×. `--format ids` ou `search` (sans les justifications)
descendent sous 300 tokens pour le même volume. `watch` ne renvoie souvent rien
du tout. Le mode `vision` (images) n'est à utiliser que sur les 2-5 finalistes
retenus après lecture du tableau compact.

## Tests

```bash
python3 -m unittest discover tests -v
```

111 tests, aucune dépendance externe (stdlib `unittest`). Ils couvrent le
parsing défensif des payloads, la taxonomie, le chargement des packs, les
statistiques robustes, chaque signal de score isolément, un scénario
bout-en-bout avec une pépite plantée et un piège de reproduction à écarter, et
le format de sortie (y compris le budget de tokens). Le transport réseau réel
**n'est pas testé ici** — DataDome bloque ce conteneur ; `lbc search "test"
--pages 1` sert de test de fumée à lancer depuis une machine personnelle.
