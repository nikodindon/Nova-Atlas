# Découverte des sources — Sprint 07

## Autorité et architecture

La découverte ne crée aucun abonnement. Le YAML du Source Manager reste la seule
autorité opérationnelle ; ni PostgreSQL, ni journal des articles, ni LLM ne sont
utilisés pour décider/écrire des sources. Aucun service n'est déployé automatiquement.

`modules/discovery/source_discovery.py` : validation, candidats typés, détection
HTML/HTTP Link, adaptateur SearchBackend, vérification et classement déterministe.
`modules/web/discovery_api.py` : API privée, admission POSIX, cache de prévisualisation
et annulation coopérative. Le JS statique du gestionnaire rend uniquement du texte.

Les téléchargements utilisent **ExternalHTTPClient**, ceux des candidats passent
par **FeedIngestor** puis le parser RSS/Atom du Sprint 05. L'injection d'un transport
est réservée aux appels Python/tests, jamais sélectionnable par une URL ou un
paramètre API. Pas de requests parallèle, navigateur explorateur ou téléchargement
intégré à feedparser. Le transport sécurisé reçoit un budget global partagé comptant
chaque tentative/hop, y compris les redirections.

Les reproductions initiales montrent que le test de flux historique rejette à raison
une page HTML mais ne sait pas en extraire un lien relatif RSS ou un HTTP Link Atom.
Ces tests restent présents ; HTML seul ne devient jamais une preuve de flux valide.

## Mode site

L'administrateur saisit une URL HTTP(S) publique sans credentials. Une page seulement
est récupérée. L'ordre des candidats est : HTTP `Link` rel=alternate de type XML
RSS/Atom, `<link rel="alternate">`, liens `<a>` explicites RSS/Atom, puis `/feed`,
`/rss`, `/rss.xml`, `/atom.xml`, `/feed.xml`. Pas de crawl des liens internes ordinaires.
Les premières déclarations explicites priment sur les chemins conventionnels.

Les URL relatives se résolvent à partir de l'URL finale vérifiée après redirection.
Les balises `<base>` et le JavaScript du site ne sont pas exécutés/interprétés.
Charset HTTP reconnu ; UTF-8 de repli avec remplacement des octets invalides.
Les encodages XML du flux restent gérés par son parser. Pas de détection universelle
d'encodages HTML historiques sans charset HTTP : certains libellés peuvent être dégradés.
Les liens privés/malformés sont écartés avant téléchargement. Les autres sont tous
revérifiés par la politique DNS/SSRF à chaque connexion.

Une URL déjà directement RSS/Atom est vérifiée sans deuxième téléchargement. Une
réponse RSS vide structurellement reconnue est `empty`, pas indisponible. Un chemin
conventionnel absent reste un candidat en erreur, jamais présenté comme existant.
RSS 0.9x/1.0/2.0 et Atom 0.3/1.0 : même support et limites XML que RSS_INGESTION.md.

## Résultats et classement

Candidat : SHA-256 de l'URL exacte, URL de flux, site d'origine, méthode, titre,
description, format, langue déclarée éventuelle, nombre d'entrées vérifiées, dernière
publication UTC fiable, catégorie suggérée (NULL tant qu'inconnue), état, avertissements,
date de vérification, trois exemples et appartenances Source Manager connues.

`unverified`, `success`, `empty`, `not_modified`, `invalid_feed`,
`temporarily_unavailable`, `permanently_unavailable`, `blocked_by_security` sont
explicitement distincts. 404/410, 429, 5xx et timeout sont visibles via code/reason.
Un 304 sans cache probant reste non vérifié ; les métadonnées de format et les entrées
d'un cache de découverte valide permettent de conserver la preuve sur 304.
HTML, XML invalide, DTD/entités et format inconnu ne permettent pas l'ajout « vérifié ».
Les candidats non essayés à cause des plafonds/annulation restent `unverified`.

Ordre stable : preuve RSS/Atom, déclaration explicite du site, correspondance de mots
du thème dans le titre du flux, date publiée fiable décroissante, absence dans les
sources, puis URL lexicographique. Le tableau affiche ces critères. Ce classement
n'est pas un jugement de qualité éditoriale ; une date future peut venir d'une source
incorrecte et reste accompagnée de l'avertissement du parser. Aucune date du moteur
de recherche n'est utilisée comme fraîcheur du flux. Aucune catégorie/langue/zone
n'est devinée. Le contenu complet des articles n'est jamais téléchargé.

Déduplication exacte des candidats. Une URL déjà active ou désactivée, y compris
dans une autre catégorie, est signalée avec ses appartenances. Les redirections
servent de signal visible, jamais de fusion, remplacement ou canonicalisation.
Les hashes MD5 et la rétention des articles restent inchangés.

## SearXNG optionnel

Désactivé si `ATLAS_SEARXNG_URL` est vide. Définir dans l'environnement privé du
processus Web un endpoint **HTTPS public** se terminant par `/search`, sans userinfo,
query ni fragment. Compose transmet cette variable au service web uniquement.
Aucune instance publique n'est imposée et aucun fournisseur payant/LLM n'est activé.
Le YAML historique ne charge pas automatiquement les fichiers .env.

L'adaptateur utilise l'[API officielle SearXNG](https://docs.searxng.org/dev/search_api) :
q borné à 2–120 caractères sans contrôles, `format=json`, première page, safesearch=1.
L'instance doit autoriser le format JSON ; son absence peut produire HTTP 403.
Le site choisi par l'opérateur peut lui-même contacter ses moteurs configurés :
revoir leur politique de confidentialité avant activation. Aucun test CI ne les contacte.

Au maximum vingt résultats du JSON sont examinés, deux sites distincts par défaut
sont retenus. Ils passent par la même découverte sécurisée. Les snippets et dates
des résultats ne servent pas de preuve. Endpoint manquant, JSON invalide, 403/429,
TLS ou timeout produisent une erreur sûre, sans corps fournisseur ni URL de requête.
Les cookies/auth/en-têtes privés ne sont jamais relayés aux sites découverts.

**LAN/Docker/Tailscale privés non pris en charge pour ce backend dans cette tranche.**
Aucun bypass de DNS/SSRF n'est ajouté. Un endpoint apparemment public qui résout en
privé est bloqué au runtime ; `search_available` signifie configuré, pas joignable.
Une future intégration interne devra avoir son transport distinct à destination
fixe et sans redirection arbitraire, avec revue dédiée. Ne pas publier une instance
privée sur Internet uniquement pour contourner cette restriction.

RSSHub : seulement le protocole d'adaptation `RSSHubBackend` est préparé. Aucune
route, instance, clé ou conteneur activé. L'implémentation est reportée : choix de
routes explicitement autorisées et frontière réseau interne demandent une revue
séparée ; pas de proxy générique construit depuis une URL utilisateur.

## Quotas et performances

Paramètres opérateur dans le bloc privé `discovery` du config.yaml ; aucun paramètre
API ne modifie les plafonds. Les valeurs sont des entiers :

| Clé | Défaut | Borne |
|---|---:|---:|
| seconds | 25 | 1–45 |
| requests | 12 | 1–20 |
| per_host | 8 | 1–12 |
| sites | 2 | 1–3 |
| links | 100 | 1–200 |
| feeds | 8 | 1–12 |
| results | 12 | 1–20 |

Une seule opération Web source active (découverte ou test ponctuel), une seule
connexion à la fois ; pas de pool de workers supplémentaire. Trois découvertes
non cachées/minute, réservation persistée **avant** le réseau. Cache identique sans
nouveau téléchargement pendant cinq minutes. Chaque hop consomme requests et per_host,
même si un quota interdit d'autres téléchargements ; on ne multiplie pas par les
redirections. Deux redirections maximum par GET, timeout ≤8 s, deadline partagée,
réponse ≤1 Mio décompressé. Borne réseau théorique par opération : requests ×1 Mio,
plus en-têtes bornés par le client. Les budgets de temps empêchent de nouvelles
requêtes après échéance ; une phase de parsing bornée déjà commencée finit avant retour.

429 et Retry-After imposent les embargos sans retry immédiat. Le journal privé
`discovery_feed_state.json` utilise FeedState ; News et tests Source Manager lisent
ses embargos au début de leur opération/cycle, et réciproquement. Les entrées/cache
ne sont pas fusionnés et les fichiers n'ont qu'un producteur chacun. Une requête
News déjà en vol n'est pas interrompue rétroactivement.

Annulation : Event vérifié avant les requêtes, entre les lectures et les candidats ;
une I/O en cours est bornée par le watchdog/timeout. Aucun traitement LLM ni article.
Limite héritée : les workers DNS libc non annulables sont bornés à quatre et peuvent
survivre à leur timeout ; voir HTTP_SECURITY.md. Pas de garantie distribuée ou NFS.
Les sessions et l'annulation restent celles d'un Web mono-processus avec threads.

## API et parcours

Toutes les routes sont privées par défaut et renvoient `Cache-Control: no-store`.
Toute opération POST, y compris preview/cancel, exige authentification et CSRF.
JSON strict ≤4 Kio, champs exacts ; aucun chemin de fichier, endpoint ou transport
n'est fourni par le client. 401/403 auth/CSRF, 400 validation/état occupé, 429 quota.

- POST `/api/admin/discovery/site` : `{ "url": "https://example.org/" }`.
- POST `/api/admin/discovery/search` : `{ "query": "technologie japonaise" }`.
- GET `/api/admin/discovery/status` : opération active, début connu, configuration search.
- POST `/api/admin/discovery/cancel` : `{}` ; arrêt coopératif de l'opération active.
- POST `/api/admin/discovery/preview` : `{ "id": "SHA256" }` ; lit uniquement le cache,
  pas de nouvelle récupération ni de réponse HTML brute.

Les découvertes sont synchrones et bornées, pas une file de jobs durable. Status
ne prétend pas connaître une progression en pourcentage. Un crash termine le job,
le prochain appel pourra relancer avec les quotas persistés.

Dans `/config/feeds/page`, section Découvrir : mode site/sujet, progression, annulation,
filtres vérifiés/nouveaux, critères, preview et choix d'une catégorie existante.
L'ajout utilise **POST /api/admin/sources** avec URL, catégorie et version SHA-256.
Aucun appel de découverte/preview n'ajoute de source. Conflit 409 : relire/reprendre
le parcours pour obtenir une nouvelle version, sans retry aveugle écrasant une édition.
Une URL exacte déjà présente dans une autre catégorie reste ajoutable explicitement.

Le gestionnaire utilise le cadre radio/nav existant. Les initialisations JS sont
connues et idempotentes ; les scripts reçus par navigation ne sont pas exécutés.
Le même objet audio survit aux allers-retours Live/Sources et aux filtres. Un résultat
arrivant après navigation met à jour seulement le composant détaché, pas la nouvelle
page. Pas d'eval, de nouveau script inline ni d'innerHTML de données externes.
La CSP du Sprint 01G reste inchangée. Les assets communs ne déclenchent aucun appel
administratif s'il n'existe pas de composant gestionnaire dans la page.

## Cache et confidentialité

`data/source_discovery.json` version 1, 0600, ≤256 Kio, dernière opération seulement,
TTL cinq minutes après retour. La prochaine admission remplace les données expirées ;
un seul fichier, pas d'accumulation. Pas de nettoyage au démarrage ni de modification
des sources. Les appels en cache recalculent version et appartenances depuis le YAML.
Après redémarrage, un cache valide reste utilisable jusqu'à expiration ; pas de reprise
persistante de job. Un cache corrompu est préservé et les nouvelles opérations sont
refusées : l'opérateur peut l'archiver puis retirer uniquement ce cache, sous arrêt
coordonné et sans effacer les embargos du journal HTTP.

Le journal `discovery_feed_state.json` a les limites 8 Mio/512 entrées/90 jours de
FeedState et ses écritures privées atomiques. Il conserve les entrées normalisées
et résumés RSS bornés nécessaires aux 304, pas les pages HTML explorées ni le contenu
complet des articles. Les métadonnées de format/titre/langue de découverte ne sont
écrites que dans ce journal, pas dans les anciens journaux News/tests.

URL de flux exactes validées sans credentials connus ; URL de site/finale affichées
sans query/fragment. Aucun secret de fournisseur accepté. Un token opaque au nom
inconnu dans une URL n'est pas détectable universellement : n'utiliser que des sources
publiques sans secrets, conserver les caches/export privés (limite du Source Manager).
Pas de logs contenant requêtes thématiques, pages, stack traces ou corps fournisseur.
Les chemins fixes sont contrôlés, fichiers spéciaux/symlinks/hardlinks refusés.

## Dépannage et rollback

Aucun résultat : consulter states/reasons ; déclarations HTML absentes, site dynamique
ou noms de chemins non conventionnels peuvent nécessiter l'URL RSS connue. Un site
sans RSS ne justifie ni crawler ni génération RSSHub implicite. Budget épuisé :
candidats unverified/erreur réseau ; augmenter les bornes opérateur uniquement après
mesure, sans désactiver les protections. SearXNG absent : utiliser le mode site.

Rollback : drainer les opérations Web, sauvegarder YAML et journaux privés, revenir
au code précédent et retirer ATLAS_SEARXNG_URL. Conserver les locks stables. Le YAML
et les journaux d'articles sont inchangés hors des ajouts explicitement confirmés.
Les nouvelles sources ajoutées restent des lignes compatibles ; comparer avant
suppression volontaire. Le code précédent ignore le cache de découverte ; ne pas
supprimer son journal d'embargos pour reprendre plus vite des requêtes 429.
Avant un rollback qui ne sait pas lire ces embargos, attendre leur expiration ou
suspendre les téléchargements concernés. Pas de promesse de jobs reprenables.

## Validation et limites

Tests hors ligne : HTML RSS/Atom, HTTP Link, WordPress, chemins non vérifiés, relatives,
redirections, XML/DTD/HTML hostile, métadonnées absentes, erreurs HTTP, SSRF/rebinding,
budgets par hop, 429 durable, SearXNG simulé, API privée/CSRF/cache/conflits/ajout explicite.
Chromium local couvre preview/ajout et radio persistante, y compris réponse tardive.
Le smoke Docker/RSS utilise son serveur local synthétique avec transport explicitement
injecté ; la validation SSRF de production reste active, aucune exception LAN.
Les smokes LiteLLM/PostgreSQL existants restent obligatoires et sans services réels.

Pas de découverte exhaustive, réseaux sociaux, RSSHub opérationnel, moteur payant,
LLM, SearXNG LAN ou consolidation de sources. Les métadonnées restent des assertions
de l'éditeur du flux, pas une preuve de fiabilité journalistique ou de disponibilité
actuelle. Les sources nécessitant cookies, JS ou auth ne sont pas prises en charge.
