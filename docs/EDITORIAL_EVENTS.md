# Événements éditoriaux — Sprint 08

## Livraison et autorité

Un événement est une **association éditoriale proposée**, pas une information
vérifiée ni un remplacement des articles. Les JSON articles, hashes MD5, catégories
historiques et journaux de reprise restent autoritaires et intacts. PostgreSQL
reste un stockage de snapshots ; aucun dual-write ou migration métier.

Le moteur est sans réseau et sans LLM :

- `modules/editorial/events.py` : couvertures/événements typés, comparaison explicable,
  contraintes négatives, groupes complets et identifiants stables.
- `index.py` et `cli.py` : index privé dérivé, reconstruction dry-run, verrous POSIX,
  écritures atomiques et corrections humaines séparées.
- `categories.py` : catégories configurées + métadonnées d'affichage historiques.
- `selection.py` : sélection facultative et provenance des paquets radio/éditions.
- `quality.py` : corpus annoté, comparaison des règles et benchmark hors ligne.
- `modules/web/events_api.py` : projection publique explicite, en lecture seule.

Aucune collecte, génération TTS/LLM, migration ou reconstruction sur les données
réelles n'a été effectuée pour ce sprint. L'index n'est **pas reconstruit au startup**,
ni lors d'une visite web. Les jobs automatiques éventuels restent une étape ultérieure.

## Audit et reproductions

| Consommateur | Avant | Après |
|---|---|---|
| Breaking News | Deux mots communs regroupaient des faits différents concernant Donald Trump | Index dérivé, ou fallback conservateur borné ; aucune fusion sur deux mots |
| Flash radio | SequenceMatcher sur 200 caractères de résumé éliminait un fait distinct avec un préfixe commun | Même signature publique conservée, élimination des enregistrements exactement identiques seulement |
| Articles | MD5 URL et seen/retries sept jours | Inchangés : identité d'article, pas preuve d'événement |
| Direct | Alternance de sources dans chaque catégorie | Conservée ; détail événementiel discret en supplément |
| Éditions | CAT_ORDER et contexte limité aux huit premières catégories | Ordre configuré/legacy puis toutes les catégories observées ; limite historique de trois articles/catégorie hors opt-in conservée |
| Rapports | Itération de CATEGORY_ORDER, catégories supplémentaires ignorées | Toutes les catégories présentes, ordre configuré puis fallback historique |
| Posts/web/flashs | Labels/icônes limités, catégorie custom refusée par les flashs | Fallback lisible et registre commun ; catégories configurées autorisées |

Deux tests écrits avant correction échouaient : faux groupe sur deux mots communs,
perte d'un article distinct sur préfixe de résumé. Les tests restent des non-régressions.
Les listes historiques de labels restent des présentations de compatibilité,
pas des listes d'exclusion des catégories.

## Couvertures, identité et dates

Une couverture conserve hash historique, URL d'origine autorisée, titre actuel,
titre original, extrait de résumé, source, hostname, catégorie, publication brute,
publication UTC éventuelle, timestamp historique brut et son statut temporel.
Les champs first_seen_at/fetched_at/processed_at sont repris **uniquement** si les
articles les fournissent explicitement avec timezone. Le timestamp historique sans
fuseau n'est pas transformé en date de récupération ou en UTC certain.

Un hash absent reçoit un substitut explicitement préfixé `derived:` ; il n'est pas
présenté comme un MD5 historique et n'est jamais écrit dans l'article original.

L'identité privée de couverture est SHA-256(hash historique, URL exacte, publication
brute, timestamp brut, catégorie, titre et résumé projetés). Un même URL/hash publié
sous plusieurs catégories ou avec un contenu différent reste représentable sans
modifier les originaux. Les copies exactement identiques dans plusieurs fichiers
partagent une couverture et conservent leurs emplacements privés. Un compteur
`article_count` compte ces couvertures distinctes : ce n'est ni un nombre de faits
vérifiés, ni une garantie d'articles éditorialement indépendants.

Projection bornée : titres 1 000 caractères, résumés 4 000, sources 200, catégorie
100. Les textes complets restent dans les JSON d'origine. URL avec credentials ou
schéma dangereux non publiée dans l'index ; aucune récupération réseau de ces URL.
Les paramètres secrets connus sont refusés par la projection. Les jetons opaques
non identifiables universellement restent la limite commune des données historiques.

EditorialEvent : event_id, titre représentatif, catégorie principale et secondaires,
article_hashes, couvertures, sources observées, hostnames distincts, compteurs,
first_discovered_at, created_at/last_updated_at, latest_published_at, event_at,
statut/confiance, désaccords possibles, raisons et version d'algorithme.

- `event_at` reste NULL : la publication ne prouve pas la date du fait réel.
- first_discovered_at reste NULL sans métadonnée explicite fiable.
- created_at/last_updated_at datent l'indexation/l'évolution du groupe, pas le fait.
- latest_published_at est une assertion de publication de la source avec fuseau.
- Les dates locales ambiguës sont conservées et empêchent le regroupement automatique.

Les IDs sont déterministes pour un groupe inchangé et indépendants de l'ordre
fourni. Lors d'une reconstruction avec l'ancien index, un événement conserve son
ID pour son descendant de plus grand recouvrement ; les égalités sont départagées
par identifiants triés. Un ajout compatible au même groupe conserve l'ID. Une fusion
humaine de plusieurs anciens groupes choisit un ID antérieur déterministe ; une
séparation peut créer de nouveaux IDs. Les IDs ne promettent pas une identité
universelle éternelle pour un événement réel dont toute la couverture change.

## Comparaison conservatrice v1

Normalisation NFKD/casse/accents, tokenisation Unicode, mots vides FR/EN, petit
lexique d'actions normalisées et ancres lexicales de mots capitalisés. Ce dernier
est une **heuristique**, pas une reconnaissance linguistique des personnes/lieux.
Les traductions déjà présentes dans title sont utilisées ; title_original reste
accessible. Aucune traduction supplémentaire n'est lancée.

Un regroupement automatique requiert simultanément :

1. Publications timezone-aware distantes d'au plus 18 h.
2. Jaccard des titres ≥0,82 et au moins six tokens significatifs communs.
3. Une ancre lexicale et une action communes.
4. Jaccard des résumés ≥0,50, avec au moins six tokens dans chaque résumé.
5. Aucune contrainte négative.

Contraintes négatives : nombres différents dans les titres ; ensembles de nombres
non vides divergents dans les résumés ; jours explicites différents dans les titres ;
actions distinctes ; annonce/démenti ou négation détectée ; fenêtre temporelle
incompatible ou publication inconnue/ambiguë. Les nombres en lettres et dates
implicites ne sont pas analysés sémantiquement. Une négation de contexte peut
provoquer une séparation conservatrice ; elle ne prouve pas qu'un article est faux.

Un article est ajouté à un groupe seulement s'il satisfait les règles **avec tous
ses membres**. Deux groupes possibles : l'article reste séparé. Pas de composantes
connexes transitives A–B–C. Cinquante membres maximum par groupe, 500 couvertures
maximum par reconstruction. Les relations incertaines restent séparées et leurs
raisons/similarités sont conservées dans l'index privé (500 paires au plus).

Les seuils appartiennent à `conservative-complete-link-v1` ; ne pas les modifier
sans versionnement et corpus de non-régression. Même avec ces règles, une formulation
quasi identique peut décrire deux faits différents ; le moteur n'a pas connaissance
directe du monde. Les angles éloignés, traductions approximatives, langues sans
segmentation bien couverte et données sans timezone réduisent fortement le rappel.

## Provenance, désaccords et confirmation

Chaque couverture originale reste consultable avec média, lien, catégories et dates.
Sources textuelles et hostnames sont distincts ; un hostname n'est pas nécessairement
un groupe de presse. Aucune consolidation Public Suffix List, agence ou syndication
n'est prétendue. Plusieurs reprises d'une dépêche ne sont pas des confirmations
indépendantes. `independent_confirmation` reste **toujours false**, y compris après
association humaine. Le champ confidence décrit une règle lexicale, pas une probabilité.

Une annonce et son démenti restent normalement séparés. Le signal
possible_denial_or_disagreement est informatif ; aucun texte n'est déclaré vrai ou
faux. Une association humaine peut réunir des perspectives contradictoires, en
conservant les raisons négatives et les liens originaux. Aucune citation nouvelle
ou synthèse factuelle n'est créée par le regroupement.

## Stockage, limites et reconstruction

`data/editorial_events.json` privé, 0600, version 1, ≤8 Mio : version d'algorithme,
manifestes/fingerprint, diagnostic, groupes et rapprochements incertains. Fichiers
articles quotidiens `YYYYMMDD_articles.json`, fenêtre de 14 dates civiles Europe/Paris
(inclusive, configurable 1–31). Jusqu'à 31 fichiers, 4 Mio/fichier, 32 Mio d'entrée,
500 couvertures. Dépassement des budgets : refus explicite, pas une publication
partielle prétendument complète. Les anciens fichiers hors fenêtre restent intacts.

JSON/fichier invalide ou article inutilisable : diagnostic privé et état degraded,
les autres documents peuvent être indexés. Les anciens sous-dossiers journaliers
radio sont signalés comme nécessitant un adaptateur explicite ; ils ne sont pas
annoncés comme déjà importés. Les fichiers futurs/hors rétention ne sont pas supprimés.
Symlinks, hardlinks et fichiers spéciaux sont refusés. Aucun chemin issu du HTTP.

Dry-run sur une **copie préparée**, layout standard :

```sh
.venv-baseline/bin/python -m modules.editorial.cli --root /copie-atlas rebuild
# Examiner input_sha, compteurs et diagnostics avant application :
.venv-baseline/bin/python -m modules.editorial.cli --root /copie-atlas rebuild \
  --apply --expect-input SHA256_EXAMINE
```

Pas de déplacement ou copie automatique des données. Les constructeurs Python
permettent des chemins articles/data explicites ; la CLI emploie root/data/articles
et root/data. Les répertoires doivent déjà exister. Le dry-run n'écrit même pas les
verrous. L'application exige le fingerprint revu, puis sérialise les rebuilds via
`editorial-events.lock` et prend le verrou producteur `article-integrity.lock`.
Attente bornée à 5 s, aucune tâche existante interrompue. Ne supprimer aucun lock.

Le manifeste est revérifié avant publication. Temp privé, flush/fsync, os.replace
et fsync du répertoire rendent visible uniquement un index complet. Deux rebuilds
coopératifs sont sérialisés. Crash avant remplacement : ancien index conservé ;
relance sûre. JSON index corrompu/version inconnue : original préservé, opération
refusée. Après revue, archiver l'index défectueux dans une copie et reconstruire
explicitement ; aucun effacement « réparateur » automatique.

L'index est un snapshot : l'API indique generated_at et les groupes leurs dates de
mise à jour. Les nouveaux articles n'apparaissent qu'après reconstruction explicite.
Il n'y a pas de garantie de synchronisation instantanée avec News. Un éditeur externe
qui ignore les verrous n'est pas couvert comme un producteur coopératif. Stockage
POSIX local et UID/GID partagés requis ; pas de promesse NFS/SMB/distribuée.

## Corrections humaines

`data/editorial_overrides.json`, privé/versionné et distinct, contient :

```json
{"version":1,"separate":[["coverage_id_A","coverage_id_B"]],
 "merge":[],"titles":{"coverage_id_A":"Titre choisi"},"history":[]}
```

La CLI reçoit seulement les champs à remplacer (`separate`, `merge`, `titles`).
Les champs omis sont conservés. Les IDs se trouvent dans /api/events ou l'index.
Un patch est un contenu JSON local explicite, jamais une URL téléchargée.

```sh
.venv-baseline/bin/python -m modules.editorial.cli --root /copie-atlas overrides \
  --patch /copie-atlas/patch.json
# Exiger le current_sha revu et une raison :
.venv-baseline/bin/python -m modules.editorial.cli --root /copie-atlas overrides \
  --patch /copie-atlas/patch.json --apply --expect-sha SHA256_EXAMINE \
  --reason 'Deux faits distincts rapprochés à tort'
```

Une contrainte de séparation prime ; les conflits directs ou transitifs de fusion
sont refusés avant écriture. Chaque application conserve un historique privé avec
raison, date et hash précédent, au plus 1 000 entrées (refus au-delà, pas d'effacement).
Les références sorties de la fenêtre ou modifiées sont conservées et signalées,
pas supprimées silencieusement. Les titres opérateur restent du texte non fiable.

Après override, l'ancien index est marqué incompatible et les lectures publiques
renvoient unavailable jusqu'au rebuild : aucune réutilisation silencieuse de la
mauvaise association. L'index enregistre le hash de l'override sans en publier
l'historique. Crash après correction avant rebuild : correction persistée, relance
explicite du rebuild. La future interface de correction est reportée ; ces commandes
non destructives constituent la première tranche opérateur.

## Catégories

Le registre fusionne ordre/catégories réellement configurés (SourceManager), labels
et icônes historiques, catégories observées dans les articles. ID original préservé,
nom lisible de repli, icône globe et couleur NULL si inconnues, état actif et ordre.
Les catégories vides/désactivées restent représentables. Défaut d'un article sans
catégorie : monde, sans réécriture du JSON source.

Le web et les flashs acceptent les catégories configurées, rapports/éditions
parcourent toutes les catégories présentes. Hors configuration explicite, leur ordre
historique est conservé. Les nouvelles catégories et celles auparavant omises sont
désormais incluses ; cela peut allonger les prompts et créer des sections auparavant
absentes. Les budgets de contexte LLM existants restent applicables. Pas d'administration
nouvelle des couleurs/icônes dans ce sprint, pas de renommage automatique d'articles.

## Sélection radio/éditions — opt-in

Configuration privée, désactivée par défaut :

```yaml
editorial_events:
  enabled: false
  max_events: 20
  importance:
    geopolitique: 1
    economie: 1
```

En activant enabled=true, les articles de la fenêtre existante sont convertis en
paquets événementiels en mémoire. Les overrides actuels sont respectés et leur hash
est enregistré. Pas d'index SQL obligatoire. Score expliqué = importance catégorie
(0–10, défaut 1) + fraîcheur de publication fiable (0–1 sur 24 h) + richesse de résumé
(0–1) + diversité de hostnames **plafonnée à 0,3**. Le compte brut d'articles ne crée
pas de bonus. Round-robin par catégorie ; source unique toujours éligible, date
inconnue ne supprime pas une information. max_events borne la sélection, pas le stock.

Chaque paquet contient un représentant et les couvertures, sources, liens, dates et
extraits associés. Les prompts opt-in indiquent « confirmation indépendante non
établie » et conservent la provenance, sans fabriquer de faits. Des extraits de
résumés sont bornés ; ce n'est pas une troncature du script audio généré. Aucun
article original ni possibilité de reprise n'est retiré du stockage.

`editorial_selection_radio.json` / `editorial_selection_editions.json` gardent la
préparation privée (dernière sélection seulement), score et preuves, sous verrou et
écriture atomique. C'est une intention de prompt, pas une preuve que le modèle a
fidèlement repris chaque phrase. Une correction concurrente s'applique à la prochaine
préparation ; elle ne réécrit pas une génération déjà en cours.

Mode désactivé : mêmes articles/fenêtres et parcours audio. Seul le défaut de
suppression fragile par préfixe est corrigé pour tous les flashs. Tags de voix,
manifestes TTS, retries, FFmpeg/FFprobe, reçus et publication atomique inchangés.
Une erreur de sélection/provenance ou un segment manquant fait échouer le bulletin,
sans publication partielle. Aucun nouveau fournisseur TTS ou prompt cloud par défaut.

`VerificationAdvisor` prépare uniquement un contrat borné désactivé vers atlas-verify.
Aucun appel fournisseur, cache LLM, audit modèle ni activation ne sont implémentés.
Activer le stub refuse explicitement. Une future PR devra fournir réponses structurées
validées, timeouts/retries/budgets et namespace modèle/prompt/langue ; une décision
LLM ne pourra jamais être la seule preuve d'association ou de vérité.

## API, Breaking et interface

GET /api/events : page≥1, per_page 1–50 (défaut 20), catégorie≤100 caractères ; count
est le total filtré. GET /api/events/<event_id> : détail ou 404. Routes publiques
**lecture seule** ; aucune reconstruction, mutation, collecte ou génération déclenchée.
Index absent : 200 unavailable/index_not_built avec liste vide ; index corrompu ou
corrections non reconstruites : 503 générique, aucun chemin local ni stack trace.
Les contrats /api/articles et Android ne changent pas.

Projection explicite : couvertures, catégories, dates/statuts, sources/hostnames,
compteurs, raisons et algorithme. Pas d'emplacement de fichier, manifeste d'entrée,
audit d'override, journal d'ingestion, configuration ou secret LLM.

Breaking News utilise l'index disponible et les publications fiables des quatre
dernières heures, rejette les dates trop futures et conserve title/link/count/sources/cat.
Les métadonnées événement/provenance sont optionnelles supplémentaires. Sans index,
fallback conservateur limité à 200 couvertures récentes et groupes d'au moins deux,
aucune reconstruction disque. Un article seul peut apparaître dans la sélection
avec index ; il n'est jamais déclaré confirmé par plusieurs médias.

Le fil affiche une section details discrète, titre représentatif, liens de chaque
couverture, catégories, dates et compteurs. Toutes les chaînes sont échappées ;
aucun script inline, eval ou insertion HTML non fiable. CSP inchangée et même objet
Audio pendant navigation/rafraîchissement. L'index n'est pas une nouvelle source
d'autorité pour les cartes d'articles, qui restent toutes disponibles.

## Qualité mesurée et limites

Corpus `tests/fixtures/editorial/pairs.json` : **24 paires synthétiques annotées
manuellement**, 8 mêmes événements, 12 différents, 4 ambigus. Beaucoup de négatifs
partagent volontairement un résumé stéréotypé : stress-test des anciennes règles,
pas échantillon représentatif de presse. Les comparateurs historiques sont évalués
comme règles de paires, sans tous les filtres temporels/tri du système d'origine.

| Comparateur | TP | Fausses fusions | Groupes manqués | Précision | Rappel | Ambigus regroupés |
|---|---:|---:|---:|---:|---:|---:|
| Deux mots communs | 8 | 12 | 0 | 40 % | 100 % | 4 |
| Préfixe SequenceMatcher | 8 | 12 | 0 | 40 % | 100 % | 4 |
| Conservateur v1 | 6 | 0 | 2 | 100 % (6/6) | 75 % (6/8) | 0 |

**Ce 100 % n'est pas une qualité globale annoncée.** Six propositions synthétiques
ne suffisent pas à estimer la précision sur des articles réels. Les deux angles
éloignés du même fait sont volontairement manqués. Un corpus représentatif et revue
humaine restent indispensables avant usage éditorial sensible.

Les checks synthétiques de redémarrage et d'ajout conservent l'ID du groupe testé.
Benchmark local Python 3.12 : 500 couvertures proches → 10 groupes plafonnés, environ
2,91 s ; corpus de paires environ 0,06 s. Pas de SLA ni garantie de performance
universelle. Rapport exact dans `docs/testing/EDITORIAL_QUALITY.json`.

```sh
.venv-baseline/bin/python -m modules.editorial.quality \
  tests/fixtures/editorial/pairs.json --benchmark 500
```

## Rollback et prochaines étapes

Désactiver d'abord editorial_events.enabled, drainer les préparations puis sauvegarder
index, overrides et manifestes privés avec les articles/config séparément. Revenir
au code précédent conserve les articles et fichiers audio intacts. Conserver les
locks stables et overrides pour une future reprise ; ne pas restaurer un ancien
JSON articles pour « annuler » un index. Le code précédent ignore le nouvel index,
mais réintroduit ses limites de regroupement/préfixe et de catégories.

Aucune donnée exclusivement écrite en PostgreSQL : pas de rollback SQL nécessaire.
Le futur schéma métier devra séparer événements, couvertures, versions/contraintes
humaines, évaluations et jobs ; ne pas réutiliser les tables de snapshots comme un
faux dual-write. Priorités suivantes : corpus réel autorisé/annoté, UI de correction,
meilleures ancres linguistiques, suivi des évolutions et jobs de reconstruction
explicitement planifiés. Pas de faits vérifiés inventés par consensus de flux.
