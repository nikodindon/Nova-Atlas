# Ingestion RSS/Atom — Sprint 05

Socle dev@1cf5352 après fusion explicitement demandée de la PR #12, huit contrôles
réussis. Aucun flux Internet, donnée de production, modèle ou TTS réel utilisé.
Le stockage opérationnel reste JSON/YAML ; aucun cutover PostgreSQL.

## Architecture et compatibilité

`external.get` sécurisé → XML strict sans DTD → feedparser sur **BytesIO** →
FeedEntry/FeedResult → journal privé de découverte → extraction/synthèse/publication
historiques. feedparser 6.0.14 et sgmllib3k 1.0.0 épinglés dans les locks runtime/dev.
Aucune URL ou chemin passé à feedparser, aucun téléchargement par sa couche HTTP.

Compatibilité testée : RSS 0.91, RSS 1.0/RDF, RSS 2.0, Atom 0.3/1.0. Les variantes
RSS 0.90/0.92/0.93/0.94 reconnues par feedparser sont également acceptées ; RSS sans
version explicite reste admis pour compatibilité. Namespaces DC/content, CDATA,
Unicode, liens alternatifs et xml:base supportés. Content-Type non XML ne fait pas
rejeter un XML valide : il produit un avertissement. Le décodage suit le document
XML validé, plutôt qu'un charset HTTP trompeur.

Le parseur lxml exige du XML bien formé, sans réseau, DTD, entités résolues, huge_tree
ou recover. DTD **refusée**, y compris une DTD RSS 0.91 historiquement courante :
ce sous-ensemble est une restriction explicite de sécurité. XML malformé ou format
non reconnu = invalid_feed, jamais un flux vide sain. Le bozo feedparser n'est pas
ignoré : seules les dégradations d'encodage/Content-Type connues sont admises après
validation XML indépendante. Pas de réparation heuristique silencieuse.

## Entrées et résultats typés

FeedEntry contient feed_url, entry_id, title, link, published_raw/published_at,
updated_raw/updated_at, author, source, category, summary_raw, content_raw,
retrieved_at et les statuts de dates. Les datetime fiables sont UTC aware.
Le titre HTML est converti en texte ; scripts/styles retirés. Summary/content bruts
restent des données non fiables, jamais du HTML à exécuter.

`legacy()` expose title/link/pub_date plus ingestion privée. Une provenance compacte (source_key, identifiant, dates, auteur/source bornés) est
stockée au niveau du record d'article_processing.json, pas dans l'article public.
Les corps summary/content restent dans le cache borné, pas dupliqués dans chaque
record de reprise. Les champs JSON consommés par le site/Android, éditions, posts et radio restent
identiques. Les prompts ne sont pas modifiés ; le contenu RSS ne remplace pas
l'extraction historique de la page. Pas de nouvelle UI Source Manager.

| FeedResult.status | Sens |
|---|---|
| success | flux reconnu, entrées utilisables (éventuellement différées par la politique d'âge) |
| empty | flux reconnu réellement sans entrée |
| not_modified | HTTP 304, relecture du snapshot validé ; cache_miss explicite si absent |
| invalid_feed | XML/structure/format invalide, limites de parsing ou aucune entrée utilisable |
| temporarily_unavailable | timeout, erreur réseau, 429/5xx ou admission différée avec motif explicite |
| permanently_unavailable | autre erreur HTTP, dont 404/410 |
| blocked_by_security | refus de la politique externe SSRF |

Métadonnées : code de motif, HTTP status, durée, avertissements, Retry-After et
entries. Un timeout n'est jamais converti en empty. Les exceptions/corps/URL bruts
ne sont pas journalisés. Les entrées manquant de titre/lien valide sont comptées ;
quelques entrées invalides dégradent explicitement le résultat, toutes invalides
font refuser le flux. Une source défaillante n'arrête pas les suivantes.

## Dates et fraîcheur

Publication et mise à jour sont lues dans leurs éléments XML distincts (pubDate,
published/issued, updated/modified ; DC date pour RSS). Les tuples UTC de feedparser
ne sont pas une preuve de timezone : ils ne servent pas à inventer un offset.
ISO 8601/RFC 3339 ou RFC 822 avec timezone fiable sont convertis en UTC. Sans offset,
la valeur reste ambiguous_local ; invalide/non standard, elle reste invalid, brute
et exploitable comme métadonnée. Une date multilingue hors formats reconnus n'est
pas réinterprétée arbitrairement. Les dates absentes/invalides/ambiguës ne font pas
supprimer une entrée. Les dates futures ont un avertissement.

retrieved_at = téléchargement HTTP 200 du document ; un 304 garde cette date des
entrées en cache. Le suivi distingue dernière tentative, succès réseau et dernière
récupération 200 saine. La première découverte reste celle du journal Sprint 01E,
jamais remplacée par une date publiée. Les timestamps publics/calendriers existants
ne changent pas ; environnement Docker Europe/Paris pour affichage éditorial,
UTC pour les nouvelles métadonnées fiables.

max_age_days=0 conserve tout par défaut. Si activé, seules les publications avec
une date UTC fiable trop ancienne sont différées avant découverte ; aucun hash seen
ou échec définitif n'est créé. Ce n'est pas une archive des entrées exclues : elles
pourront être reprises si encore disponibles après changement de politique. Les
validateurs sont invalidés lorsque la politique d'âge/fenêtre change, pour retester
le document ; une date douteuse reste éligible.

## Identités et ordre

Le hash historique reste MD5(link.strip()). Les GUID/Atom ID sont des métadonnées
privées, pas une nouvelle clé de déduplication. Une URL présente dans plusieurs
flux/catégories suit le premier ordre YAML, comme auparavant ; un seul traitement
sur sept jours. Même GUID avec une nouvelle URL = une nouvelle URL, sans fusion
silencieuse. Une entrée mise à jour avec même URL ne régénère pas automatiquement
un résumé publié dans la fenêtre historique ; consolidation future Sprint 08.

Liens alternatifs HTML préférés, self/enclosures non sélectionnés comme article.
URLs relatives résolues avec xml:base/URL finale. Les identifiants URN sans lien
n'autorisent pas une extraction. Un GUID RSS permalink HTTP valide peut fournir le
lien selon le parser ; un GUID non-permalink seul ne doit pas être inventé comme URL.
Schémas dangereux, URLs malformées/avec credentials et IP privées littérales refusés.
DNS et chaque redirect d'article restent validés au téléchargement par external.
Pas de suppression de paramètres de tracking, réécriture de fragment, changement
d'URL canonique ou fusion de contenus. L'URL absolue initiale reste l'identité de
l'article même si l'extraction suit une redirection sécurisée.

## Cache conditionnel et santé privée

Nouveau fichier privé **data/feed_ingestion_state.json**, version 1, remplacé
atomiquement (0600) dans data_dir, partagé dans les binds existants. Records indexés
par SHA-256 de l'URL configurée complète ; changer d'URL ne réutilise pas un cache
voisin. Chaque record garde au besoin :

- last_attempt, last_success, last_good_fetch, http_status, error/status, failures ;
- next_attempt_at, host/host_until, rate_host, retry_until, duration et warnings ;
- last_count, latest_published_at parmi les entrées retenues ;
- etag, last_modified, response_url, policy et snapshot d'entrées normalisées.

Le snapshot permet de rejouer un 304 après crash/redémarrage ou décalage de quota
catégorie. Un 304 sans snapshot porte cache_miss et force une future requête sans
validateurs ; il ne prétend pas avoir retrouvé des entrées. Un 200 corrompu garde
l'ancien état sain et ses validateurs. Un 200 XML valide vide est un état sain.

Les validateurs ne sont ajoutés qu'à **l'URI exacte** dont ils proviennent, même
au milieu d'une chaîne de redirects. Ils ne sont pas transmis à un autre domaine
ou chemin. Seuls If-None-Match et If-Modified-Since sont configurables via un type
fermé, avec valeurs ASCII bornées sans contrôles ; aucun header arbitraire, cookie,
Authorization, netrc ou proxy n'est introduit.

L'état peut contenir des URL à query privée et du HTML brut : le garder privé,
hors Git, ne pas l'exposer via API/export public/log. Les diagnostics utilisent un
hash court, statut/motif et comptes ; jamais le corps, l'URL ou l'exception brute.
Le snapshot SQL du Sprint 04 ne capture pas ce cache HTTP facultatif ; le sauvegarder
séparément si nécessaire, sans le confondre avec le journal d'articles autoritaire.

Rétention des sources inactives : 90 jours, sauf embargo serveur encore actif.
Limite normale 512 records, au plus trois admissions simultanées supplémentaires ;
cache global 8 Mio. Éviction d'un record complet avec ses validateurs, pas d'ETag sans
snapshot. Les embargos futurs ne sont pas raccourcis par la rétention ; à saturation
protégée, nouvelles sources différées, caches de corps évincés avant les délais.
Un cache corrompu/symlink/incompatible est préservé et désactive la persistance
conditionnelle ; la collecte peut continuer sans validateurs, le journal métier
reste indépendant. Corriger sur copie à l'arrêt plutôt que remplacer des données.

FeedIngestor.invalidate(url) permet au futur Source Manager d'invalider explicitement
les validateurs/snapshot d'un record, sans raccourcir ses embargos de retry. Pas de nouvelle route admin. Intervention manuelle : drainer/arrêter les
collecteurs, sauvegarder le cache privé, modifier une copie validée ou le conserver
à part, puis redémarrer. Une invalidation totale provoque de nouveaux téléchargements,
pas l'effacement des articles/seen. Ne pas supprimer les fichiers .lock.

## Concurrence, politesse et arrêt

Quatre téléchargements au maximum, un par hostname par défaut. Un ordonnanceur
borné évite de remplir tous les workers avec des attentes pour le même hôte. Un
budget HTTP supplémentaire couvre **chaque destination réelle**, redirects compris,
et l'extraction d'article partage ce budget. Pas de limitation par IP/CDN partagé
entre plusieurs noms indépendants ; ce n'est pas un ordonnanceur distribué.

Seules les récupérations/parsing de flux sont parallèles. Les résultats sont repris
dans l'ordre configuré ; découverte, journal, extraction, LLM et publication restent
séquentiels sous article-integrity.lock. Aucune source de vérité SQL ajoutée. Pas
de chevauchement de collecteurs autorisé par ce verrou ; scheduler/LLM inchangés.

Un seul GET logique par tentative (redirects bornés). Pas de boucle de retry réseau
immédiate. Backoff par source : min(cap, base*2^(échecs-1)). 404/410 et sécurité : au
moins 24h avant nouvelle vérification de la même URL. Retry-After secondes/date HTTP
prime même au-delà du cap. 429 crée aussi un embargo de l'hôte effectivement contacté,
appliqué avant la libération du créneau HTTP et persisté pour les autres flux/restarts.
Les délais de source ne constituent pas des retries agressifs de sécurité.

Arrêt : le scheduler transmet son Event à fetcher.run ; plus de nouvelles requêtes
ni appels LLM après observation du stop. Les tâches HTTP actives drainent dans leurs
budgets, les futures non admises sont annulées ; ThreadPoolExecutor est rejoint.
Une annulation observée avant génération ne consomme pas le budget de tentatives
de l'article ; un résumé déjà obtenu peut être publié sans nouvelle traduction.
Pas de worker de téléchargement abandonné. Limite héritée du résolveur libc : un
DNS bloqué n'est pas annulable, au plus quatre workers DNS daemon peuvent terminer
après leur deadline. Les sockets/watchdogs et limites SSRF restent celles du Sprint
01B. Une annulation n'atteste pas l'arrêt d'un serveur distant.

## Configuration

Dans la section rss existante, options facultatives :

```yaml
rss:
  max_articles_per_feed: 8 # 1..100, fenêtre par flux comme avant
  feed_workers: 4         # 1..4
  feed_per_host: 1        # 1..2, au plus feed_workers
  feed_timeout: 10        # 1..60 s, total incluant redirects/attente HTTP
  feed_max_sources: 256   # 1..512 uniques par cycle ; surplus explicitement différé
  feed_min_poll_seconds: 60
  feed_retry_base_seconds: 300
  feed_retry_cap_seconds: 21600
  max_age_days: 0
```

Timeouts TLS/DNS/body, ports, IP spéciales/CGNAT, pinning DNS-to-socket, gzip et taille
restent sécurisés dans external.py : 4 Mio de corps transféré/décompressé ; XML ≤25k
nœuds et ≤1000 items/entries. Titre/lien ≤4096, dates brutes ≤1024 ; summary/content
bruts tronqués à 16k avec avertissement. Pas de troncature silencieuse du script
éditorial ou de la réponse LLM. Les limites de fenêtre/âge sont explicites dans les
warnings, pas présentées comme un historique exhaustif du flux.

feeds.yaml reste la source de vérité, chargeur/format/commentaires désactivés et
ordre conservés. Cache de config désormais lié au chemin + mtime_ns ; reload invalide
le cache. Fichier absent/illisible : liste vide, sans retour automatique à des sources
hardcodées. Les reprises déjà journalisées continuent même si aucun flux actif.
Modifier les chemins de stockage seulement après arrêt coordonné, comme Sprint 01E.

## Dépannage, tests et rollback

Consulter statut/motif, HTTP status, prochaine tentative, date de dernier 200 sain et
cache_miss. Pour 429, attendre l'embargo plutôt que changer artificiellement l'URL.
XML invalide/DTD/aucune entrée utilisable : corriger côté source ou revoir une copie
hors ligne, ne pas désactiver la protection SSRF/XML. Les versions RSS/Atom supportées
n'impliquent pas l'acceptation de tous les fichiers XML tolérés par feedparser seul.

Tests entièrement synthétiques : familles RSS/Atom, dates, 200/304, persistance,
backoff, DNS/redirect, concurrence/arrêt, reprises et contrats publics historiques.
Le smoke Docker exécute un faux serveur RSS/Atom **dans un conteneur network=none** ;
un transport explicitement injecté ne contacte que sa loopback, sans option de
production autorisant le LAN. Il vérifie le chemin Nova, 304/restart/déduplication
et le rejet de redirects privés. Aucun vrai flux, LLM, TTS ou service de production.

Rollback : drainer/arrêter les collecteurs, sauvegarder le cache privé et le journal,
revenir au code précédent avec son lock/runtime et garder articles/seen/journal
intacts. Le cache de flux nouveau est ignoré par l'ancien code. Les métadonnées
additionnelles privées sont ignorées par le chargeur historique. Ne pas restaurer
un ancien jeu d'articles par-dessus les publications plus récentes. Ce rollback
réintroduit l'absence d'Atom et les limites du parser historique ; aucun rollback
ou changement de production exécuté pendant ce sprint.

Références : [feedparser 6.0.14](https://feedparser.readthedocs.io/en/latest/),
[dates](https://feedparser.readthedocs.io/en/latest/date-parsing/),
[bozo](https://feedparser.readthedocs.io/en/latest/reference-bozo/).
