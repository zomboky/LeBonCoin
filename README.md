# GoldDigger02 — recherche leboncoin, détection de pépites & vérification externe

Outil en ligne de commande, pensé pour être piloté par un agent aussi bien que
par un humain. Le principe : **tout le travail coûteux se fait en Python** —
récupération des annonces, regroupement en cohortes comparables, statistiques,
extraction de marque/modèle, scoring, et (nouveau) extraction propre de pages
web externes — pour que l'appelant (humain ou modèle) ne reçoive qu'une
short-list compacte, jamais du HTML ou du JSON brut.

Anciennement `lbc`. Intègre directement le cœur de
[**Mach2**](https://github.com/zomboky/mach2) (clone local de Firecrawl) comme
module `research`, pour vérifier sur le web une référence identifiée dans une
annonce avant de lui faire confiance.

## Installation

```bash
pip install -r requirements.txt
# Optionnel mais recommandé : repli navigateur (DataDome, ou research --render).
pip install playwright && playwright install chromium
```

Python 3.10+. Aucune base de données externe : SQLite est créé automatiquement
sous `~/.cache/golddigger02/`.

## Avertissement réseau : DataDome (concerne `deals`/`search`/`watch`/`show`)

leboncoin est protégé par DataDome. **Ces commandes ne fonctionneront pas
depuis une IP de datacenter, un VPS ou un CI** — DataDome bloque ces plages
avant même d'atteindre le code (vérifié : 403 sur l'API,
`ERR_CONNECTION_RESET` sur Chromium via proxy). Fait pour tourner sur une
machine personnelle avec une connexion résidentielle normale.

La commande `research` n'est **pas concernée** : elle lit des pages web
ordinaires (Wikipédia, forums, résultats d'enchères...), jamais leboncoin.

Comportement de `deals` en cas de blocage :
1. requête HTTP directe sur l'API interne (rapide, peu coûteux) ;
2. si 403 : ouverture ponctuelle de Chromium (Playwright) pour récolter un
   cookie `datadome` valide, puis retour en HTTP pour la suite ;
3. si l'API reste fermée : lecture du `__NEXT_DATA__` de la page de recherche.

Les cookies sont mis en cache (`~/.cache/golddigger02/state/cookies.json`) :
un seul passage navigateur suffit généralement pour toute une session.

L'usage prévu est personnel et raisonnable — pas de rate-limiting imposé
(option `--delay`, désactivée par défaut), mais pas de parallélisme agressif
ni de contournement de blocage au-delà d'un cookie légitime.

## Usage rapide

```bash
# Un pack thématique entier (lance toutes ses requêtes-sources)
python3 -m golddigger02 deals --domain aviation --top 20

# Une recherche libre
python3 -m golddigger02 deals "leica m6" --category telephonie --where 75 --max-price 800

# Uniquement les nouveautés depuis le dernier passage
python3 -m golddigger02 watch --domain michelin

# Détail d'une annonce, avec images
python3 -m golddigger02 show 123456789 --domain aviation

# Vérifier un modèle identifié sur le web (ne touche pas à leboncoin)
python3 -m golddigger02 research "https://fr.wikipedia.org/wiki/..." --filter "Gueneau 123" --max-chars 2000
```

## Commandes

| Commande | Rôle |
|---|---|
| `deals` | chercher + classer par potentiel de pépite |
| `search` | recherche simple, sortie compacte, sans scoring |
| `watch` | comme `deals`, restreint aux annonces jamais vues |
| `show <id>` | détail d'une annonce, avec images |
| `domains [nom]` | lister ou inspecter les packs thématiques |
| `research <urls...>` | pages web → markdown propre en fichiers (ex-Mach2) |
| `seen` | statistiques / purge de la mémoire des annonces vues |

Options principales de `deals`/`search`/`watch` : `--category` `--min-price`
`--max-price` `--where` `--radius` `--sort` `--seller private|pro` `--pages`
`--top` `--min-score` `--min-cohort` `--format tsv|jsonl|ids|vision` `--unseen`
`--delay` `--no-cache` `--no-browser` `--from-fixture <json>` (rejoue un JSON
local, utile pour tester sans réseau).

**Options de couverture, opt-in — sans elles, comportement inchangé** :

| Option | Effet | Coût |
|---|---|---|
| `--all-categories` | interroge toutes les catégories du pack, pas seulement la première (ex. aviation : `collection`+`decoration`+`bricolage`) | ×(nb de catégories) |
| `--typo-queries` | ajoute les fautes d'orthographe connues du pack comme requêtes-sources (une marque mal orthographiée n'apparaît dans aucune recherche à l'orthographe correcte) | + (nb de fautes hors variantes d'accent) |
| `--sweep` | balaye une catégorie entière, sans mot-clé, triée par date — seule façon de trouver ce qui ne contient aucun de vos mots-clés. Exige `--category` ou `--domain`. `--pages` passe à 10 par défaut (contre 2) | ×10 pages, mais 1 seule requête par catégorie |

Ces options multiplient le nombre de requêtes envoyées à un site qui bloque
déjà les IP de datacenter (voir plus haut) : `aviation --all-categories
--typo-queries` ≈ 144 requêtes pour un run complet. `--sweep
--all-categories` est la combinaison la moins chère (≈ 30 requêtes) — c'est
celle à privilégier pour élargir la couverture sans se faire bloquer.
Recommandé avec `--delay`.

Options de `research` : `--filter "requête"` (ne garde que les passages
pertinents) `--max-chars N` `--render` (rendu JS, Playwright) `--concurrency N`
(défaut 5) `--no-cache` `--show N` (aperçu console de la 1ère page).

## Comment le score de pépite est calculé

Une pépite n'est pas seulement une annonce pas chère : le cas le plus payant
est celui où **le vendeur ignore ce qu'il vend**. Le score combine :

| Signal | Ce qu'il capte |
|---|---|
| `price_gap` | écart sous le prix de référence (cohorte, historique persisté, ou barème du pack), atténué si la cohorte est dispersée |
| `hidden_model` | modèle identifié dans la description, absent du titre |
| `authenticity` | marqueurs d'origine trouvés ; un marqueur de reproduction écrase le score |
| `brand_tier` | pièce recherchée du pack vendue au prix du tout-venant |
| `typo_brand` | marque mal orthographiée → invisible dans les recherches normales |
| `weak_listing` | annonce bâclée (peu de photos, description courte) |
| `urgency` | vente pressée (indices forts : succession, débarras...) ou vendeur qui admet ne pas identifier l'objet ; les indices faibles et omniprésents (« vieux », « en l'état »...) sont plafonnés et ne saturent jamais seuls le signal |
| `price_drop` | baisse de prix observée depuis un passage précédent (50% de rabais = signal plein) |
| `private_seller` | un particulier price en général moins juste qu'un pro |
| `freshness` | annonce récente |

**Prix de référence — ordre de précédence.**
1. **Cohorte vivante** : sur les annonces comparables de ce run (même catégorie
   — ou même pack si celui-ci déclare plusieurs catégories équivalentes —,
   même modèle extrait ou signature de titre), la médiane des *autres*
   annonces si la cohorte en compte au moins `--min-cohort` (5 par défaut,
   avec au moins 2 « autres » dans tous les cas).
2. **Historique persisté** : la médiane pleine cohorte des runs précédents
   (mélangée par moyenne mobile — un run isolé la déplace de 30%, pas plus —
   et bornée par le barème du pack quand un motif correspond, pour qu'aucune
   dérive ne dépasse ce qu'un humain a jugé plausible). C'est ce qui donne une
   référence chiffrée au collector rare, là où seul un barème existait avant.
   Péremption : 90 jours (`GD2_COHORT_STAT_TTL`).
3. **Barème du pack** (`value_bands`), à défaut des deux précédents.

La médiane et l'écart utilisent MAD (écart absolu médian), robuste aux prix
aberrants qui pullulent sur leboncoin ; la dispersion de la cohorte module
aussi la confiance accordée à `price_gap` — un rabais dans une cohorte serrée
compte plus que le même rabais dans une cohorte étalée.

**Note sur le bruit.** Promouvoir l'historique persisté au-dessus des barèmes
(délibérément conservateurs) relève en général les prix de référence sur le
rare, donc produit plus de candidats détectés — et plus de faux positifs.
C'est l'effet recherché, mais à garder en tête en lisant les résultats.

**Garde-fous.** Un marqueur de reproduction détecté réduit le score à 15% de sa
valeur, quel que soit le prix. Un prix sous 5% de la référence est traité comme
suspect (erreur de saisie, pièce détachée, arnaque) plutôt que comme une
affaire.

## Packs thématiques

Un pack (`golddigger02/domains/*.yml`) décrit un terrain de chasse : catégories
API, requêtes-sources, marques, règles de détection de modèle, fautes
d'orthographe connues, marqueurs d'origine/reproduction, et barèmes de prix.

Livrés : `aviation`, `space` (conquête spatiale), `flight-sim` (matériel de
simulateur de vol), `militaria`, `michelin` (Michelin collector), `tech-vintage`
(informatique/électronique ancienne rare), `vintage-clothing`, `collector`
(générique).

**Ajouter un domaine ne demande aucun code** : déposer un fichier dans
`~/.config/golddigger02/domains/mon-pack.yml` (variable `GD2_DOMAIN_DIR` pour
changer l'emplacement). Un pack qui porte le nom d'un pack livré le remplace.

```yaml
name: mon-pack
label: Description humaine
categories: [collection, decoration]      # noms ou ids leboncoin (golddigger02/taxonomy.py)
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

Voir `golddigger02/domains/aviation.yml` pour un exemple complet.

## Module `research` (ex-Mach2) — vérification externe

Le pipeline `deals` travaille sur du **JSON structuré** (API leboncoin ou
`__NEXT_DATA__`) : il n'y a jamais de page éditoriale à nettoyer, donc jamais
besoin d'extraction markdown à cet endroit. Le module `research`
(`golddigger02/research.py`) répond à un besoin différent, en aval : quand
`hidden_model` ou `typo_brand` fait remonter une référence obscure (« Gueneau
123 », « Kollsman »), vérifier sa rareté/valeur en lisant une page web
ordinaire — forum de collectionneurs, résultat d'enchères, fiche technique.

C'est un portage direct du cœur de Mach2 (`trafilatura` pour isoler le contenu
principal d'une page et le convertir en markdown, écriture en fichiers,
résumé compact en console). Deux différences volontaires :

- **`scrape`/`batch` seulement** — pas `map` (découverte d'URLs d'un site) ni
  `crawl` (aspiration récursive). Ils ne servent à rien pour vérifier un objet
  précis et auraient ajouté du poids sans usage réel ici.
- **Cache unifié** — réutilise la table `responses` du cache SQLite de
  l'outil (celle qui sert déjà à l'API leboncoin) plutôt que le cache fichier
  séparé de Mach2. Un seul mécanisme de persistance dans tout GoldDigger02.

```bash
python3 -m golddigger02 research <url1> <url2>... \
  --filter "terme du modèle à vérifier" --max-chars 2000
```

Écrit un `.md` par page (front-matter + contenu) et un `manifest.json` dans
`~/.cache/golddigger02/research/<horodatage>/` ; la console n'affiche qu'un
résumé (titre, nb de mots, chemin, ou l'erreur). `--render` bascule sur
Playwright pour les sites qui affichent leur contenu en JavaScript.

Workflow recommandé pour l'agent : `WebSearch` (outil Claude) pour trouver les
URLs pertinentes → `research --filter "<modèle>"` pour les récupérer en
fichiers → lire uniquement les `.md` utiles.

## Mémoire des annonces vues

SQLite (`~/.cache/golddigger02/cache.sqlite3`), deux tables distinctes :
- `responses` : cache des réponses HTTP (leboncoin **et** `research`), TTL
  configurable (`GD2_CACHE_TTL`, 6h par défaut pour leboncoin, 24h pour
  `research`) — évite de refetch la même page ;
- `seen` : chaque annonce déjà affichée par `deals`/`watch`. `watch` (alias de
  `deals --unseen`) ne renvoie que ce qui n'y figure pas encore, souvent zéro
  ligne au bout de quelques passages.

`golddigger02 seen` affiche les statistiques par pack. `golddigger02 seen
--forget <id...>` ou `--forget-domain <pack>` ou `--all` réarment la mémoire.

L'accès concurrent (le mode `research --concurrency N` interroge le cache
depuis plusieurs threads) est protégé par un verrou explicite dans `cache.py`
— sqlite3 refuse par défaut qu'une connexion traverse un thread sans ça.

## Budget de tokens (mesuré, pas estimé)

Sur une fixture de 500 annonces, `deals --top 20 --format tsv` produit environ
**900-950 tokens** de sortie (une ligne par annonce, colonnes + justification
courte), contre plusieurs centaines de milliers pour le JSON brut équivalent
— un facteur ~20-50×. `--format ids` ou `search` (sans les justifications)
descendent sous 300 tokens pour le même volume. `watch` ne renvoie souvent rien
du tout. Le mode `vision` (images) n'est à utiliser que sur les 2-5 finalistes
retenus après lecture du tableau compact.

`research` suit le même principe : le contenu complet va sur disque, la
console ne renvoie qu'un résumé d'une ligne par URL (titre, nb de mots,
chemin) — jamais la page entière dans le contexte de l'agent.

## Tests

```bash
python3 -m unittest discover tests -v
```

177 tests, aucune dépendance réseau (stdlib `unittest`). Ils couvrent le
parsing défensif des payloads, la taxonomie, le chargement des packs, les
statistiques robustes, chaque signal de score isolément, un scénario
bout-en-bout avec une pépite plantée et un piège de reproduction à écarter, le
format de sortie (y compris le budget de tokens), la migration du schéma
SQLite (base pré-versionnage, idempotence), l'historique de prix, la
précédence et le corridor anti-dérive de la référence de cohorte persistée, la
matrice requêtes × catégories (`--all-categories`), et le module `research`
(extraction markdown, filtrage par pertinence, écriture de fichiers, et un
test de régression sur l'accès concurrent au cache SQLite). Le transport réseau
réel vers leboncoin **n'est pas testé ici** — DataDome bloque ce conteneur ;
`golddigger02 search "test" --pages 1` sert de test de fumée à lancer depuis
une machine personnelle. Le module `research`, lui, a été vérifié manuellement
en conditions réelles (Wikipédia, example.com) en plus de la suite committée.
