# GoldDigger02 — améliorer la détection de pépites

## Context

L'outil cherche des annonces leboncoin sous-évaluées, en pariant sur le cas où **le
vendeur ignore ce qu'il vend**. L'audit du pipeline (`api` → `extract` → `pricing` →
`score` → `render`, 131 tests OK) montre que le code est sain mais que la chasse est
bridée par deux choses : **ce qui n'est jamais récupéré**, et **trois signaux qui ne
comptent pas réellement**.

Constats vérifiés en exécutant le pipeline sur `tests/fixtures/aviation_sample.json`
(`rank(..., min_cohort=2)`) :

| id | score | prix | réf | source | authenticity | freshness |
|---|---|---|---|---|---|---|
| 1001 | 62.17 | 25 | 220 | cohort | **1.0** | 0.0 |
| 1004 | 6.46 | 240 | 220 | cohort | **1.0** | 0.0 |
| 1003 | 2.42 | 220 | 220 | cohort | 0.0 | 0.0 |
| 1002 | 0.36 | 300 | 179 | band | 0.0 | 0.0 |

- `authenticity` vaut 1.0 sur deux annonces et **pèse zéro** : la clé manque dans
  `config.WEIGHTS`, et `score.py:123` fait `.get(name, 0.0)`.
- `freshness` est à 0 pour **les quatre** annonces : la fixture est datée
  `2026-08-04`, on est le 2026-08-17. Ce test perd de la couverture chaque jour.
- La médiane « leave-one-out » est décrite dans le commentaire `pricing.py:64-66`
  mais **pas implémentée**.
- `robust_z` / `cohort_dispersion` sont testés et **jamais appelés**.
- `cli.py:112` et `:117` : `params.category = domain.category_ids[0]` — `aviation.yml`
  déclare `[collection, decoration, bricolage]` et 2/3 ne sont jamais interrogés.
- `domain.typos` ne sert qu'au scoring d'annonces **déjà récupérées** — or la requête
  qui les a ramenées utilisait l'orthographe correcte. `typo_brand` (poids 1.2) est
  quasi inatteignable.
- `mark_seen` (`cache.py:167`) écrase `price` → **toute baisse de prix est perdue**.

**Résultat visé** : plus de candidats récupérés, un classement juste sur ces
candidats, et un nouveau signal fort (la baisse de prix). Tout élargissement réseau
est **opt-in par drapeau** — un run existant ne change ni de coût ni de résultat.

## Ordre d'exécution : Lot 2 → Lot 1 → Lot 3 → Lot 4

Pas l'ordre numéroté. Le Lot 2 est hors-ligne, sans schéma, et couvert par les tests
existants. Le Lot 1 coûte des requêtes réelles contre un site qui bloque déjà
(`aviation` avec les deux drapeaux = **144 requêtes**). Tripler le volume d'entrée
d'un scoreur dont `authenticity` vaut zéro ne produit que plus de sortie fausse.

**Contrainte d'ordre interne** : C2 (signature de cohorte) doit précéder D2, qui
persiste des lignes indexées par `cohort_key`.

---

## Étape 0 — Échelle de migration SQLite (`cache.py`)

Prérequis des lots 3 et 4, sans changement de comportement. Les lots n'ajoutent que
des *tables*, donc `CREATE TABLE IF NOT EXISTS` suffirait aujourd'hui — installer
l'échelle quand même, car au premier `ALTER TABLE` il n'y aura aucun mécanisme face à
des bases utilisateurs vivantes.

Remplacer `executescript(_SCHEMA)` (`cache.py:61`) par un `_migrate()` piloté par
`PRAGMA user_version` (vit dans l'en-tête du fichier, atomique, pas de bootstrap) :

- `user_version == 0` → base neuve **ou** antérieure au versionnage : rejouer
  `_SCHEMA` (idempotent, `IF NOT EXISTS` partout), marquer 1.
- puis appliquer les migrations `2..N`.
- Deux pièges : `PRAGMA user_version = ?` **n'accepte pas de paramètre lié** (il faut
  interpoler `int(version)`) ; `executescript()` commit implicitement — acceptable
  dans `__init__`, jamais en cours d'écriture.

Migration 2 : tables `price_history` (Lot 3) et `cohort_stats` (Lot 4).

**Tests** — nouveau `tests/test_cache_migration.py` (idiome déjà présent à
`tests/test_research.py:160` : `cache.Store(path=Path(tmp) / "test.sqlite3")`) :
- `test_ancienne_base_conserve_ses_annonces_vues` — écrire l'ancien schéma + une ligne
  `seen` en `sqlite3` brut avec `user_version=0`, puis ouvrir un `Store`. **Le test le
  plus important du plan : la migration ne détruit pas de données vivantes.**
- `test_base_neuve_est_a_la_version_courante`, `test_migration_idempotente`.

---

## Lot 2 — Correction du scoring (hors-ligne, aucun réseau)

### A1 — Poids `authenticity` (`config.py`, `score.py`)

Ajouter `"authenticity": 1.0`. Justification : **sous `typo_brand` (1.2)** — une faute
d'orthographe est un signal de *découverte* (personne d'autre ne peut voir l'annonce),
les marqueurs d'origine un signal de *validation*. **Au-dessus de `weak_listing`
(0.8)** — « plaque constructeur / n° de série » sépare un instrument à 300 € d'une
résine à 15 €. C'est le pendant positif de `repro_markers`, qui écrase déjà le score
×0.15 ; une asymétrie où le négatif est un multiplicateur dur et le positif vaut zéro
est indéfendable.

**Ne pas ajouter d'`assert set(signals) <= set(WEIGHTS)` à l'exécution** — faire
planter le run d'un utilisateur pour une faute de frappe dans un dict est pire que le
zéro silencieux. `.get(name, 0.0)` reste. L'instrument correct est un test :
`assertLessEqual(set(listing.signals), set(config.WEIGHTS))`.

**Effet dénominateur — à documenter.** `total_weight` passe de 9.9 à **11.9** avec
`authenticity` + `price_drop`, soit une déflation uniforme ×0.832 de tous les scores.
Vérifié : 1001 atterrit vers 60, `test_hidden_model_and_cheap_price_scores_high`
(assert `> 50`) vers 57, `min_score=50` du e2e passe encore — avec moins de marge.

**Ne pas « corriger » en normalisant sur les seuls poids applicables** : une annonce
sans historique serait divisée par un dénominateur plus petit et scorerait *plus haut*
qu'une annonce identique qui en a. Les scores doivent rester comparables entre
annonces. Accepter la déflation uniforme.

Table finale : `price_gap` 3.0, `hidden_model` 2.0, `brand_tier` 1.4, `typo_brand` 1.2,
**`authenticity` 1.0**, **`price_drop` 1.0**, `weak_listing` 0.8, `urgency` 0.6,
`freshness` 0.5, `private_seller` 0.4.

### C1 — Dé-saturer `urgency` (`score.py:22-36, 107-115`)

Trois listes au lieu de deux : `STRONG_URGENCY` (succession, débarras, vide maison,
déménagement…), `STRONG_IGNORANCE` (« je ne sais pas », « à identifier », « trouvé
dans », « appartenait à mon »…), `WEAK_TELLS` (vieux, en l'état, sans garantie, non
testé, urgent, grenier…).

```python
signals["urgency"] = _clamp(0.5 * strong + min(0.45, 0.15 * weak))
```

Un indice fort = 0.5 ; deux = saturation, ce qui est correct car « succession » +
« je ne sais pas ce que c'est » **est** le motif de la pépite. Le plafond `min(0.45,…)`
est tout l'objet de C1 : aucune accumulation d'indices banals n'atteint seule le haut
du barème.

**Élaguer les entrées incluses l'une dans l'autre**, sinon l'unité de 0.5 ne veut plus
rien dire : `"debarras"` est une sous-chaîne de `"a debarrasser"`, et `"je ne sais
pas"` de `"sais pas ce que c'est"` — les deux se déclenchent sur la même phrase et
comptent double. Supprimer les variantes longues.

**Garder `urgency` à 0.6** — la dé-saturation baisse mécaniquement la valeur moyenne du
signal, donc on pourrait le monter à 0.8 pour compenser. Ne pas le faire : le
dénominateur bouge déjà dans ce lot, une variable à la fois.

**Ne pas déplacer ces listes dans le YAML des packs maintenant** — 8 fichiers à éditer
pour zéro gain immédiat. Bonne idée, mauvais moment.

### C2 — `cohort_signature` (`extract.py:55-68`)

**Rejeter l'approche par fréquence documentaire** : elle exige de faire transiter des
statistiques de corpus jusque dans une fonction pure appelée seule par
`tests/test_extract.py:131`, et rend la signature non déterministe pour une annonce
isolée. Sur-ingénierie.

**Retenir : classer puis tronquer** — `domain` est *déjà* un paramètre de la fonction
et discrimine mieux qu'une df. Un helper `_token_rank(token, domain)` renvoie une clé
de tri : marque du pack (0,0) < modèle du pack (0,1) < token contenant un chiffre
(réf/P-N/millésime) < le mot le plus long. Puis :

```python
words = sorted(set(content_tokens(listing.title)), key=lambda t: _token_rank(t, domain))[:4]
return f"{prefix}:" + "_".join(sorted(words))
```

Trier par pouvoir discriminant → tronquer → **re-trier alphabétiquement pour la
jointure**, pour une clé stable quel que soit l'ordre d'entrée et les ex æquo.

**Collision Lot 1 × Lot 2 — le point non évident, il inverse la fonctionnalité.**
`cohort_signature` préfixe par `listing.category_id`. Or `aviation.yml` déclare trois
catégories *précisément parce que le même objet physique atterrit dans des catégories
différentes*. Dès que `--all-categories` existe, un même altimètre Badin listé en
Collection et en Décoration tombe dans **deux cohortes distinctes** → les cohortes se
fragmentent, plus d'annonces passent sous `min_cohort` et retombent sur les barèmes.
`--all-categories` **dégraderait** la qualité des cohortes. Correction, dans le même
commit que C2 :

```python
prefix = domain.name if (domain is not None and len(domain.category_ids) > 1) else listing.category_id
```

Sémantiquement juste : un pack qui déclare trois catégories *affirme* qu'elles
contiennent le même genre de biens.

### A2 + A3 — Médiane leave-one-out + dispersion (`pricing.py`, `models.py`, `score.py`)

Livrer ensemble : même boucle, même liste `others`.

Nouveaux champs `Listing` (bloc « Renseignés par pricing.py / score.py ») :
`cohort_median` (médiane pleine cohorte, self inclus — pour D2),
`reference_dispersion` (MAD des *autres*, `None` si barème/historique),
`previous_price` (rempli par `cli` — pour D1). Ajouter les deux premiers à `to_dict()`
pour garder `--format jsonl` débogable.

**A2 — règle sur `min_cohort`** : `len(priced) >= min_cohort` **ET**
`len(others) >= max(2, min_cohort - 1)`.

- Garder le seuil *visible* sur la cohorte observée : `--min-cohort 5` se lit « au
  moins 5 annonces comparables existent », et `cohort_size` (affiché par `render`)
  doit continuer à vouloir dire ça. Exiger `min_cohort + 1` relèverait la barre en
  douce et réduirait la couverture sur le rare — exactement la mauvaise direction.
- Le plancher `max(2, …)` tue la pathologie que le docstring `pricing.py:64-66`
  redoute vraiment : une cohorte de deux où chacun déclare l'autre « prix du marché ».
  Avec `min_cohort=2`, une cohorte de 2 retombe désormais sur le barème. Le LOO seul
  n'aurait pas corrigé ça.

Vérifié contre les tests existants : `test_cohort_median_used_when_enough_members`
(6 membres, min 5 → 5 autres ≥ 4) passe ; `test_falls_back_to_value_band_below_min_cohort`
retombait déjà ; la cohorte Badin du e2e (3 membres, min 2 → 2 autres ≥ 2) passe.

Utiliser `m is not listing`, **pas** `m.id != listing.id` : les ids peuvent être vides
dans les fixtures construites à la main.

**A3 — la dispersion comme modificateur de confiance.** `price_gap` (`pricing.py:92`)
**ne change pas**, ses six tests non plus. Ajouter un enrobage :

```python
def gap_confidence(listing) -> float:
    dispersion = listing.reference_dispersion
    if not dispersion or dispersion <= 0:
        return 1.0            # barème, historique, ou cohorte parfaitement uniforme
    z = abs(robust_z(float(listing.price or 0), float(listing.reference_price), dispersion))
    return max(MIN_GAP_CONFIDENCE, min(1.0, z / GAP_CONFIDENCE_Z))

def price_signal(listing) -> float:
    return price_gap(listing) * gap_confidence(listing)
```

Constantes dans `config.py`, surchargeables par env comme le reste :
`GAP_CONFIDENCE_Z = 2.0` (un z robuste de 2 ≈ « nettement hors du peloton », là où on
veut le crédit plein) et `MIN_GAP_CONFIDENCE = 0.35` (un vrai rabais de 70 % ne doit
jamais être effacé parce que la cohorte est bruyante — atténué, pas supprimé).

**La branche `dispersion <= 0` porte du sens, ce n'est pas du rembourrage
défensif** : `mad([100,100,100,100,10000]) == 0.0`, ce que prouve déjà
`test_pricing.py:24`. Sans le retour anticipé, une vraie affaire dans une cohorte
*parfaitement uniforme* — la cohorte la plus fiable qui soit — serait amortie de 65 %.
Dispersion nulle = « pas d'information » = confiance pleine.

Cela **préserve exactement le chemin barème** : bande → `reference_dispersion = None`
→ confiance 1.0 → `price_signal == price_gap`.

Câblage `score.py:61-67` : garder `raw_gap = price_gap(listing)` pour le seuil de
raison `> 0.15` et pour la porte `brand_tier > 0.2` (`score.py:90`), afin que les
justifications affichées restent véridiques ; `signals["price_gap"] = price_signal(listing)`.

`robust_z` et `cohort_dispersion` cessent d'être du code mort.

### Tests du Lot 2

`tests/test_score.py` : `test_tout_signal_produit_a_un_poids` (A1) ;
`test_indices_faibles_seuls_ne_saturent_pas` (≤ 0.45 — le bug C1 exactement) ;
`test_deux_indices_forts_saturent` ; `test_un_indice_fort_vaut_plus_que_quatre_faibles`.

`tests/test_extract.py` : `test_signature_insensible_a_l_ordre_des_mots` — **utiliser
plus de 4 tokens de contenu, sinon le bug ne se reproduit pas** ;
`test_signature_retient_le_token_le_plus_discriminant` ;
`test_pack_multi_categories_regroupe_les_categories`. Aucun test existant n'affirme
une signature exacte, c'est sans risque.

`tests/test_pricing.py` : `test_la_reference_exclut_l_annonce_elle_meme` — cohorte
`[10,10,10,100,100]`, `min_cohort=5`, un « 10 » doit obtenir `55.0` et non `10.0`.
**La plupart des cohortes donnent la même médiane avec et sans self ; il faut choisir
des prix où elles diffèrent, sinon le test passe sur du code cassé.** Plus :
`test_cohort_size_compte_toujours_l_annonce` ;
`test_cohorte_de_deux_retombe_sur_le_bareme` ; `test_la_dispersion_attenue_l_ecart` ;
`test_le_bareme_n_est_pas_attenue` ; `test_dispersion_nulle_n_attenue_pas`. Les six
tests `TestPriceGap` existants restent **inchangés** : le contrat est gelé.

---

## Lot 1 — Couverture, opt-in (`api.py`, `cli.py`)

### `api.fetch_matrix` — garder `SearchParams` mono-catégorie

**Ne pas faire de `SearchParams.category` une liste.** `SearchParams` correspond 1:1 à
une charge utile API — c'est son rôle documenté (« Rien du format brut ne doit fuir
plus haut »). Une liste obligerait `filters()` à inventer un encodage multi-catégories,
et **ne peut pas fonctionner pour `web_url()`**, qui prend authentiquement une seule
catégorie. On obtiendrait un objet params exprimant quelque chose que le repli
navigateur ne peut pas honorer : une divergence silencieuse entre les deux chemins,
le pire bug possible dans un scraper.

Le fan-out est un problème **d'ordonnancement**, pas de sérialisation → couche fetch.
Bonus : une clé de cache par charge utile, donc un run `--all-categories` réutilise les
pages déjà mises en cache par un run mono-catégorie.

```python
def fetch_matrix(queries, base, categories=None, transport=None, store=None,
                 use_cache=True, on_query=None) -> list[Listing]:
    """Produit cartésien requêtes × catégories, fusionné par identifiant."""
```

Deux choix délibérés : `on_query` **garde son contrat à un argument** et reçoit un
libellé pré-formaté (le changer casserait la lambda `cli.py:113` pour du cosmétique) ;
`queries or [""]` pour que le balayage du Lot 4 réutilise cette fonction sans boucle
propre. `fetch_many` est conservé et délègue à `fetch_matrix` — compatibilité.

### Drapeaux (`cli.py`)

`--all-categories` et `--typo-queries`, tous deux `action="store_true"`, défaut
`False`. `_collect` garde `params.category = domain.category_ids[0]` sur le chemin par
défaut : **comportement identique au bit près sans les drapeaux.**

### Filtrer les variantes d'accent

La recherche leboncoin est insensible aux accents, donc une entrée `typos` qui ne
diffère que par un accent produirait une requête **strictement identique** à une
requête existante. Sur `aviation.yml`, `altimetre: altimètre` est le seul cas (8 → 7
requêtes) — `helicoptaire`, `ejectible`, `kolsman`, `bendics`, `crouset`, `jaegger`,
`kollsmann` sont de vraies fautes. Peu de gain ici, mais c'est un garde-fou gratuit
pour les packs futurs :

```python
def _typo_queries(domain) -> list[str]:
    return [w for w, right in domain.typos.items() if normalize(w) != normalize(right)]
```

### Coût — l'afficher dans `--help`

`aviation` avec les deux drapeaux : `(17 + 7) × 3 = 72` requêtes × 2 pages =
**144 requêtes**, contre un site qui bloque. Mettre la multiplication dans le texte
d'aide et recommander `--delay` dans le README. **Ne pas ajouter de plafond
`--max-queries`** : un bouton que personne ne réglera correctement, et qui masque le
coût au lieu de le montrer.

### Tests — nouveau `tests/test_api_matrix.py`

Faux transport avec une méthode `search(payload, web_url=None)` enregistrant les
charges utiles.
- `test_toutes_les_paires_requete_categorie_sont_interrogees` — l'ensemble des
  `(keywords.text, category.id)` égale le produit cartésien. **Le bug `[0]` : aucune
  paire silencieusement perdue.**
- `test_deduplication_sur_toute_la_matrice`, `test_la_premiere_occurrence_gagne`.
- `test_fetch_many_inchange_en_mono_categorie` — exactement `len(queries)` recherches.
- `test_un_blocage_sur_une_paire_n_arrete_pas_la_matrice`.
- `test_les_variantes_d_accent_ne_produisent_pas_de_requete`.

---

## Lot 3 — Historique de prix (D1)

### Faire parvenir l'historique au scoreur sans `Store`

**Un simple champ sur `Listing`, rempli par `cli`. Aucun paramètre ajouté à
`score_listing` ni `rank`.** `score.py` ne doit jamais importer `cache` ; mais faire
transiter un `dict` par `rank()` *et* `score_listing()` est pire — deux signatures
changées pour une donnée qui appartient à une annonce.

`listing.previous_price` est **exactement l'idiome déjà utilisé par les tests**
(`tests/test_score.py:37` fait `listing.reference_price = …`). Le scoring reste une
fonction pure des champs du Listing, déterministe et testable hors ligne.

`cache.py` : `previous_prices(ad_ids) -> dict[str, float]` (prix le plus élevé observé
antérieurement, chunké comme `seen_ids`) et `record_prices(listings)`.

**Ordre dans `cmd_deals` : lire l'historique d'abord, enregistrer ensuite**, sinon le
prix courant de chaque annonce devient son propre historique.

**Enregistrer sur l'ensemble collecté, pas sur l'affiché.** `mark_seen` ne tourne que
sur `ranked[:top]` et seulement si `not args.no_mark` : une annonce classée 47e hier
qui divise son prix par deux aujourd'hui n'aurait aucune référence — précisément
l'annonce que `price_drop` existe pour attraper. Appeler `record_prices` sur la liste
complète, sans condition. `--no-mark` concerne la mémoire de *veille*, pas
l'observation des prix.

### Schéma

`price_history(ad_id, price, seen_at)`, `PRIMARY KEY (ad_id, price)`, `DO NOTHING` :
une ligne par prix *distinct* et par annonce, bornée par le nombre de re-tarifications.
Compromis assumé : un prix qui oscille 100 → 90 → 100 conserve le `seen_at` d'origine.
**Rejeter la série temporelle complète** — croissance non bornée pour zéro signal
supplémentaire.

Conséquence heureuse : `mark_seen`'s `ON CONFLICT DO UPDATE SET price` (`cache.py:167`)
**n'a pas besoin d'être modifié** — `seen.price` reste « prix courant », l'historique
vit dans sa propre table. Bonne séparation, et on ne touche pas à un chemin chaud.

### Le signal (`score.py`)

```python
def price_drop(listing) -> float:
    prev = listing.previous_price
    if not prev or not listing.price or prev <= 0 or listing.price >= prev:
        return 0.0
    return _clamp((1.0 - listing.price / prev) / 0.5)
```

**Poids 1.0, seuil 50 % pour le signal plein.** Un vendeur qui baisse son prix est le
signal de *timing* le plus actionnable : vendeur motivé **et** annonce invendue, donc
personne n'a mordu. Mais c'est orthogonal à « sous-évalué » — passer de 900 à 700 sur
un objet à 300 € reste cher, et `price_gap` porte déjà la cherté absolue. D'où : sous
`hidden_model` (2.0, un avantage de découverte), à égalité avec `authenticity` (1.0,
également corroborant plutôt que découvrant). 50 % plutôt que 30 % car les petites
démarques sont de l'entretien d'annonce, pas de la motivation.

### Tests

`test_cache_migration.py` : `test_chaque_prix_distinct_est_historise` ;
`test_un_prix_repete_ne_grossit_pas_la_table` ;
`test_previous_prices_renvoie_le_plus_haut_prix_anterieur` ;
`test_mark_seen_ecrase_le_prix_courant_mais_l_historique_survit` — **le bug identifié**.

`test_score.py` : `test_baisse_de_prix_detectee` ;
`test_absence_d_historique_est_neutre` (0.0, **pas une pénalité** — une annonce
inédite n'est pas punie de n'avoir pas de passé) ; `test_une_hausse_n_est_pas_une_baisse`.

Plus le test de pureté, qui tient **à la fois** le Lot 3 et le Lot 4/D2 :

```python
def test_score_ne_depend_pas_du_cache(self):
    """Aucun objet de cache.py ne doit fuir dans score.py."""
    import golddigger02.cache as cache_mod, golddigger02.score as score_mod
    leaked = [n for n, v in vars(score_mod).items()
              if getattr(v, "__module__", "") == cache_mod.__name__]
    self.assertEqual(leaked, [])
```

---

## Lot 4 — Ratissage aveugle (B3) et référence persistante (D2)

### B3 — un drapeau sur `deals`, pas une sous-commande

`--sweep`. Tout l'aval est identique : classement, `--unseen`, `--format`,
`--min-score`, `mark_seen`, la gestion `Blocked`/`TransportError`. Une sous-commande
duplique `_add_search_options` + `_add_ranking_options` + tout le corps de `cmd_deals`
pour zéro différence de comportement ; la seule chose qui change est **la construction
des requêtes-sources**, soit une branche dans `_collect`. De la structure pour la
structure.

Trois comportements forcés, chacun justifié :
- **`sort = "date"`** — le tri par pertinence n'a aucun sens sans mot-clé, et l'ordre
  chronologique est ce qui rend un balayage répétable face à la mémoire `seen`.
- **Catégorie obligatoire** — un balayage sans mot-clé *et* sans catégorie, c'est
  « télécharger leboncoin ». Échouer bruyamment, en français, comme l'erreur de pack
  inconnu (`cli.py:98`).
- **Pagination plus profonde** — `--pages 2` = 70 annonces, inutile. Passer `--pages` à
  `default=None` et résoudre dans `_params_from_args` : **10 pages en mode sweep**
  (350 annonces/catégorie) — assez pour couvrir environ une journée de nouveautés,
  assez peu (10 requêtes) pour tourner toutes les heures. Un `-p` explicite l'emporte
  toujours.

`--sweep --all-categories` sur aviation = **30 requêtes** : c'est la combinaison *pas
chère*, et celle que les utilisateurs devraient réellement lancer. À dire dans l'aide.

### D2 — précédence : cohorte vivante (LOO) > historique persisté > barème > rien

- **Cohorte d'abord** : donnée de marché actuelle sur cette clé exacte, mesurée ce run.
- **Historique ensuite** : *même nature* de preuve (des prix réellement observés),
  simplement plus ancienne. Strictement meilleur qu'une estimation humaine.
- **Barème en dernier** : c'est un a priori humain délibérément large —
  `{match: "altimetre|variometre|…", low: 60, high: 400}` est un facteur 6.7, et le
  docstring de `ValueBand.reference` admet lui-même son conservatisme.

**Avertissement de sensibilité** : les barèmes étant pessimistes par construction,
promouvoir les médianes persistées au-dessus d'eux va globalement *relever* les prix de
référence, donc relever `price_gap`, donc produire **plus de touches et plus de faux
positifs** sur le rare. C'est l'effet recherché (le rare ne peut aujourd'hui produire
aucun signal prix), mais c'est une hausse réelle du bruit — à signaler dans le README.

### Quatre garde-fous contre la dérive

Le vrai risque n'est pas que l'outil influence les prix de leboncoin : c'est le **biais
de sélection** (on n'observe que les cohortes que nos requêtes ramènent, et `--sweep` /
`--typo-queries` changent cet échantillon) et l'**empoisonnement** (un mauvais run
écrit une médiane fausse qui persiste).

1. **Ne persister que les cohortes ayant atteint `min_cohort`.** Une cohorte de deux
   pépites n'entre jamais dans la table. Le garde-fou le plus important.
2. **Persister `cohort_median` (self inclus), pas `reference_price` (LOO).** C'est ici
   que mord la collision A2 × D2 : les références LOO sont par-annonce et chacune
   exclut délibérément un membre différent ; les réécrire figerait un biais
   par-annonce dans une estimation de population. D'où le champ séparé `cohort_median`.
3. **Mélanger, ne pas écraser** — EWMA `alpha = 0.3` : `new = 0.7*old + 0.3*observed`.
   Un run anormal déplace la valeur de 30 %, pas de 100 %, et se corrige tout seul.
4. **Borner dans le corridor du barème** : `min(max(persisted, band.low), band.high)`.
   Le garde-fou anti-dérive le plus fort, et il est gratuit — le barème curé à la main
   cesse d'être un *repli* et devient un **corridor de vraisemblance pour la valeur
   apprise**. Aucune dérive ne peut sortir de ce qu'un humain a jugé plausible.

Plus une péremption `GD2_COHORT_STAT_TTL`, **défaut 90 jours** : assez long pour qu'un
objet rare trimestriel garde une référence, assez court pour qu'un marché effondré se
corrige en une saison.

**Rejeter** la détection de changement de régime, les intervalles de confiance et la
pondération par observation : EWMA + corridor + TTL font déjà trois mécanismes pour une
estimation bâtie sur une douzaine d'annonces.

### Faire parvenir les stats persistées à `assign_reference_prices`

Les clés de cohorte sont calculées *dans* `assign_reference_prices`, donc `cli` ne peut
pas les chercher avant. **Retenir la version deux temps, avec un dict simple** :
`pricing.assign_cohort_keys(listings, domain) -> list[str]` (fine enveloppe autour du
`build_cohorts` existant, qui pose déjà `listing.cohort_key`), puis
`store.cohort_medians(keys)`, puis `rank(..., cohort_stats=stats)`, puis
`store.update_cohort_stats(listings, …)`. `rank` recalcule `build_cohorts` en interne :
c'est pur et bon marché (de la tokenisation sur quelques centaines de titres).

Rejeté : passer un *callable* `lookup(keys)` dans `rank` (injecte de l'I/O dans le
graphe d'appel de `score.py`, ce que le test de pureté interdit) ; stocker sur le
Listing comme `previous_price` (la référence est par *cohorte*, et l'annonce ignore sa
cohorte avant `build_cohorts` — même problème d'œuf et de poule).

Les deux lectures de persistance arrivent donc en **données pures** : un `float` pour
l'historique de prix, un `dict[str, float]` pour les stats de cohorte. `cache` n'est
jamais importé sous `cli`.

```python
def assign_reference_prices(listings, domain=None, min_cohort=None,
                            cohort_stats: dict[str, float] | None = None) -> None
def rank(listings, domain=None, min_cohort=None, min_score=0.0, top=None,
         cohort_stats: dict[str, float] | None = None) -> list
```

Défaut `None` des deux côtés → tous les sites d'appel et tests existants inchangés.

### Deux détails faciles à manquer

- **`reference_source` gagne une troisième valeur** : `"cohort" | "history" | "band" | ""`.
  `score.py:66` fait aujourd'hui un binaire `"cohorte" if … else "barème"` — sans un
  cas `"historique"`, toute référence persistée sera étiquetée « barème » en sortie.
- **Versionner la clé** : C2 change `cohort_signature` et atterrit avant D2, donc la
  table ne verra que des clés v2. Ajouter quand même `extract.SIGNATURE_VERSION = 2` et
  stocker `f"{SIGNATURE_VERSION}:{cohort_key}"`, pour que le prochain changement de
  signature soit un incrément de constante plutôt qu'un effacement manuel de base.

### Tests

`test_cli_flags.py` (nouveau, via `build_parser()` + `_collect` avec faux transport) :
`test_sweep_produit_une_requete_vide_par_categorie` ;
`test_sweep_sans_categorie_echoue_clairement` (`SystemExit`) ;
`test_sweep_pagine_plus_profond_sauf_si_pages_fourni` ;
`test_sans_drapeau_seule_la_premiere_categorie_est_interrogee` — **le garde-fou de
non-régression de tout le Lot 1**.

`test_pricing.py` : `test_mediane_persistee_utilisee_quand_la_cohorte_est_trop_petite` ;
`test_la_cohorte_vivante_prime_sur_l_historique` ;
`test_l_historique_prime_sur_le_bareme` ;
`test_la_mediane_persistee_est_bornee_par_le_bareme` (persisté 20, barème 60–400 →
référence 60) — **le test le plus important du lot**.

`test_cache_migration.py` : `test_seules_les_cohortes_fiables_sont_persistees` ;
`test_la_mediane_persistee_se_melange_a_l_observation` (EWMA bouge de 30 %) ;
`test_une_statistique_perimee_est_ignoree` ;
`test_on_persiste_la_mediane_pleine_pas_la_reference_loo` — **la collision A2 × D2**.

`test_render.py` : `test_source_historique_est_libellee`.

---

## Collisions entre lots — récapitulatif

| # | Collision | Résolution |
|---|---|---|
| 1 | **A2 (LOO) × D2** écrivent tous deux `reference_price` | Une chaîne de précédence unique dans `assign_reference_prices`. Réécrire `cohort_median` (self inclus), **jamais** la valeur LOO |
| 2 | **D1 × D2** ajoutent tous deux une lecture de persistance dans le scoring | Un seul motif : `cli` lit le Store et passe des **données pures** (float pour D1, `dict` pour D2). Verrouillé par `test_score_ne_depend_pas_du_cache` |
| 3 | **Lot 1 (`--all-categories`) × C2** | Non évident et **inverse la fonctionnalité** : le préfixe `category_id` fragmente en trois la cohorte d'un même objet. Corrigé dans C2 en préfixant par `domain.name` quand le pack déclare > 1 catégorie |
| 4 | **C2 × D2** | C2 doit précéder, et la clé stockée porte `SIGNATURE_VERSION` |
| 5 | **A1 × D1** déplacent tous deux `total_weight` | 9.9 → 11.9, déflation uniforme ×0.832. Livrer poids et signal dans le même commit, documenter pour les usagers de `--min-score` |
| 6 | **D1 × `--no-mark`** | Si l'enregistrement vit dans `mark_seen`, `watch --no-mark` ne construit jamais de référence. Enregistrer sur l'ensemble collecté, sans condition |
| 7 | **Lot 1 × B3** sur le volume réseau | 144 requêtes vs 30. Les deux passent par `fetch_matrix`, le coût est visible au même endroit |

## Écarté volontairement

- `SearchParams.category` en liste — casse `web_url()` irrémédiablement.
- `assert` à l'exécution sur les poids — un test, pas un crash utilisateur.
- `cohort_signature` par fréquence documentaire — sur-ingénierie ; `domain` suffit.
- Sous-commande `sweep` — duplique deux groupes d'options pour une branche.
- Normaliser le score sur les seuls poids applicables — rend les scores incomparables.
- `price_history` en série temporelle complète — croissance non bornée, zéro gain.
- Détection de changement de régime sur `cohort_stats` — trois mécanismes suffisent.
- Déplacer `URGENCY_KEYWORDS` dans le YAML **maintenant** — 8 fichiers, gain nul.
- `--max-queries` — un bouton mal réglable qui masque le coût.
- **Churn sur les tests qui passent** : `aviation_domain()` est déjà dupliqué
  (`test_score.py:11`, `test_extract.py:19`). Ajouter `tests/support.py`
  (`listing(**kw)`, `aviation_domain()`) et l'utiliser dans les **nouveaux** modules
  seulement ; ne pas toucher aux trois fichiers existants.

## Hors périmètre, mais à ouvrir en ticket

La fixture e2e est datée en dur `2026-08-04` : `freshness` vaut **déjà 0 pour ses
quatre annonces**. Ce test perd de la couverture chaque jour. Rendre les dates
relatives à `now()`.

---

## Vérification

Après **chaque** lot :

```bash
cd H:/claude-projects/leboncoin
python -m unittest discover tests -v      # 131 tests actuels + les nouveaux, tous verts
```

Contrôles de non-régression spécifiques :

```bash
# 1. Le comportement par défaut est inchangé (aucun drapeau) — comparer avant/après
python -m golddigger02 deals --domain aviation --from-fixture tests/fixtures/aviation_sample.json \
  --min-cohort 2 --format jsonl

# 2. Baseline mesurée AVANT les lots (à conserver pour comparaison) :
#    1001=62.17  1004=6.46  1003=2.42  1002=0.36
#    Après A1+D1 : déflation ×0.832 attendue, 1001 ≈ 60, l'ordre doit être identique.

# 3. Le classement ne doit jamais s'inverser sur la fixture : 1001 premier,
#    1002 (piège REPRO) dernier.

# 4. Migration sur base vivante — sauvegarder puis ouvrir :
cp ~/.cache/golddigger02/cache.sqlite3 /tmp/cache.bak
python -m golddigger02 seen        # doit afficher les mêmes stats qu'avant
sqlite3 ~/.cache/golddigger02/cache.sqlite3 "PRAGMA user_version;"   # -> 2
```

Test de fumée réseau (**machine personnelle uniquement** — DataDome bloque les IP de
datacenter), à faire une fois le Lot 1 livré, en commençant petit :

```bash
python -m golddigger02 search "test" --pages 1                        # transport vivant ?
python -m golddigger02 deals --domain michelin --pages 1 -v           # défaut, 1 catégorie
python -m golddigger02 deals --domain michelin --all-categories --pages 1 -v --delay 2
python -m golddigger02 deals --domain aviation --sweep --pages 2 -v --delay 2
```

`-v` affiche chaque requête via `on_query` : vérifier de visu que la matrice
requêtes × catégories est bien celle attendue, et qu'aucune paire ne manque.

Pour le Lot 3, l'historique de prix demande **deux runs espacés** pour produire un
signal ; le valider d'abord en unittest, puis en réel en injectant une ligne
`price_history` à la main avant un second passage.
