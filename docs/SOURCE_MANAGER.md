# Source Manager (Sprint 06)

## Architecture et autorité

`modules/core/source_manager.py` est un service Python sans dépendance Flask ni
connexion réseau sur lecture/mutation. `modules/web/source_api.py` fournit les
adaptateurs administratifs, `assets/feeds.js` rend uniquement des données textuelles.
`config/feeds.yaml` reste la seule autorité opérationnelle. Pas de dual-write SQL,
de migration au démarrage, de découverte, de redémarrage automatique ou de collecte
à l'ouverture de la page. La santé vient de `FeedState` du Sprint 05.

Audit réalisé sur le code et les fixtures, sans lire les données privées réelles :
l'ancien chargeur ignorait des lignes malformées, le writer réécrivait cet état
partiel, les routes faisaient séparément load/edit/save, un reorder acceptait des
URL répétées. Trois reproductions échouaient avant correction et restent testées.

## Modèle et YAML historique

Source : `id`, URL exacte, catégorie, `enabled`, nom facultatif, `added_at` facultatif.
La projection ajoute position, santé, observation/âge, ancienneté, dernier succès,
code HTTP, échecs, entrées, prochaine vérification, erreur et avertissements.

À la première lecture, l'identifiant est SHA-256(catégorie + NUL + URL exacte).
Une même URL dans deux catégories représente deux appartenances administratives.
Le téléchargement reste dédupliqué par URL exacte et les articles par MD5 historique.
Les identifiants ne dépendent pas de la position. À chaque écriture, les métadonnées
sont conservées dans un commentaire `# atlas-source {"id":...,"name":...,"added_at":...}`
après l'entrée : renommage, déplacement ou changement d'URL conservent cet ID.
Une suppression puis un nouvel ajout peut réutiliser l'ID initial ; pas d'identifiant
de job ni d'historique d'événements promis. Un import dans un autre fichier attribue
les identités de membership correspondantes et ne peut détourner un ID existant.

Le format opérationnel reste catégorie → liste d'URL, avec commentaires `[SPRINT0]`
pour les désactivées. Les deux conventions historiques `- # [SPRINT0] URL` et
`# [SPRINT0] URL` sont lues. Catégories vides, Unicode, ordre et URL exacte préservés.
Les commentaires généraux sont conservés en tête, leur emplacement original n'est
pas garanti. L'écriture ajoute les commentaires de métadonnées, sans conversion
vers un autre schéma YAML ; l'action utilisateur déclenche cette écriture, jamais
le démarrage. Une sauvegarde du texte précédent précède chaque modification.

Grammaire volontairement bornée : mapping simple, scalaires et commentaires ; pas
de multiline, d'alias, d'ancres, de tags ni de clés dupliquées. Une liste historique
non compatible doit être corrigée sur une **copie** puis vérifiée avant installation.
Les mutations refusent tout le document malformé, jamais une liste vide à écraser.
La lecture historique `load_feeds` garde son fallback dégradé avec avertissement
pour le moteur ; ce fallback n'est jamais utilisé par les mutations Source Manager.
Un `save_feeds` legacy sur fichier existant exige le snapshot versionné de load.

Catégories : 1–100 caractères, pas de contrôles, syntaxe YAML dangereuse ou HTML.
Nom : 160 caractères, texte non fiable rendu avec textContent. URL : 2 048 caractères,
HTTP(S), ports/politique du client sécurisé, pas de credentials ni paramètres
identifiés comme secrets (`token`, `api_key`, `key`, `sig`, etc.). Pas de
canonicalisation du tracking, de la casse ni des fragments. Une URL DNS n'est pas
résolue à la sauvegarde : chaque téléchargement valide/résout/épingle la destination.
Les adresses IP privées, locales, Tailscale/Docker et les noms locaux explicites
sont refusés. Un fichier contenant des URLs privées ou credentials reste intact,
mais l'API échoue fermée : correction locale nécessaire, pas d'export nettoyé ambigu.
Un token opaque dans un chemin ou un paramètre au nom inconnu n'est pas détectable
universellement : l'opérateur doit n'importer que des URL de sources publiques sans
secrets. Les exports administratifs sont à conserver privés.

## API et permissions

Toutes ces routes exigent la session administrateur existante. Toute mutation,
y compris test et preview, exige CSRF (`X-CSRF-Token`). Erreurs 401/403 conservées.
Réponses sources `Cache-Control: no-store`. Pas de token dans les pages publiques.

| Méthode et route | Contrat |
|---|---|
| GET `/api/admin/sources` | page ≥1, per_page 1–100 (50), q, category, health ; sources, total filtré, catégories, stats globales, version |
| GET `/api/admin/sources/<id>` | Détail et version courante |
| POST `/api/admin/sources` | category, url, name/enabled optionnels, version obligatoire |
| PATCH `/api/admin/sources/<id>` | url/name/enabled/category explicitement autorisés, version |
| DELETE `/api/admin/sources/<id>` | version ; suppression physique explicite de l'appartenance |
| POST `/api/admin/sources/actions/enable` ou `disable` | id, version ; retrait logique sans effacer la santé |
| POST `.../actions/move` | id, direction up/down **ou** catégorie existante, version |
| POST `.../actions/reorder` | category, liste complète ids sans répétition, version |
| POST `.../actions/category` | category, version |
| POST `.../actions/rename_category` | category, new_category, version |
| GET `.../stats` | Comptes exacts, distribution des états observés |
| POST `.../test` | url ; récupération ponctuelle, aucun ajout |
| GET `.../export?format=yaml\|opml` | Liste portable, accès authentifié |
| POST `.../import` | content, format ; dry-run par défaut |

Corps JSON strict (pas de doublons de clés/NaN/champs inconnus), limite HTTP
64 Kio sauf import authentifié et vérifié CSRF (JSON ≤2 Mio, contenu ≤1 Mio).
Le service Python accepte un document ≤1 Mio ; 512 sources
actives + désactivées. News conserve sa propre borne configurable `feed_max_sources`
(256 par défaut) : ajuster explicitement si la liste active dépasse ce seuil.
Version = SHA-256 du texte exact courant. Mutations modernes exigent une version
hexadécimale non nulle ; un conflit renvoie 409 et nécessite une relecture.
ID absent : 404 ; validation : 400. La CSP et les protections de proxy restent inchangées.

Les routes `/config/feeds`, `/add`, `/remove`, `/toggle`, `/move`, `/category`,
`/reset` restent des adaptateurs. `remove` conserve le retrait **logique** historique,
`toggle` permet aussi la réactivation ; l'UI utilise DELETE pour « Supprimer ».
Sans version, un adaptateur applique uniquement son action sur l'état lu sous verrou ;
les bascules/déplacements résolus depuis une lecture intermédiaire vérifient sa version.
Les erreurs de fichier malformé/privé sont désormais HTTP 400 plutôt qu'un succès
partiel. Les contrats JSON publics et Android n'ont pas changé.

## Test ponctuel et politesse

Utilise `FeedIngestor` → `modules/http/external.py` → `parse_feed(bytes)` ; pas de
réseau intégré feedparser, pas de HTML/crawler. Même DNS pinning, TLS, redirections,
ports, timeouts, taille 4 Mio et limites XML que la collecte.

Un test actif au maximum par stockage POSIX, admission persistante de cinq nouvelles
récupérations par minute et attente minimale de 60 s par URL saine. Timeout global
10 s ; jusqu'à 100 entrées normalisées (bornes du parser inchangées), trois titres
exemples. Les requêtes différées ne font aucun téléchargement. Durée, état typé,
code HTTP, format reconnu (RSS/Atom), avertissements et date UTC fiable sont retournés.
Le format n'est pas reconstruit sur un 304 ; champ null plutôt qu'une estimation.
URL finale affichée sans query/fragment ; aucun corps RSS intégral renvoyé.
Un refus de validation URL renvoie 400 explicite ; un refus DNS/redirect renvoie
`blocked_by_security`. Les 304/429/5xx/timeouts/XML invalides restent distincts.

`data/source_test_state.json` version 1 réutilise le schéma borné `FeedState`, avec
cache conditionnel/embargos propres aux tests et écritures atomiques privées.
`config/source-tests.lock` empêche deux processus de tester simultanément. Cette
séparation évite qu'un worker Web écrase l'état News pendant une collecte.
Les tests lisent les embargos News ; News lit les embargos hôte des tests au début
du cycle suivant. Une requête déjà en vol n'est pas annulée rétroactivement.
Un état de limitation corrompu refuse les tests, sans l'écraser. Les mêmes limites
90 jours/8 Mio/512 entrées du Sprint 05 s'appliquent. Jamais de boucle de retry immédiat.
Le backend d'authentification reste celui du déploiement Web **mono-processus** ;
les verrous partagés ne rendent pas ses sessions mémoire compatibles multi-workers.

## Santé et interface

healthy/unchanged/empty/degraded/temporarily_unavailable/permanently_unavailable/
blocked_by_security/never_checked/disabled viennent de la dernière observation.
Des avertissements sur un succès donnent degraded ; un état illisible est signalé
`health_state_unavailable`, pas une fausse absence de vérification.
La date et l'âge sont affichés ; `stale` après 24 h n'affirme jamais une panne
actuelle. Dernier succès antérieur ne masque pas une erreur plus récente.
Désactivation/réactivation/catégorisation ne touchent pas au journal de santé ni
aux embargos 429. Le test ponctuel est présenté séparément, sans prétendre remplacer
l'observation du dernier cycle News.

`/config/feeds/page` : recherche, filtres catégorie/santé, pagination, observations,
activation/désactivation, nom, déplacement, suppression, ajout, test et import/export.
Toutes les données externes utilisent textContent/DOM contrôlé, aucun eval/HTML reçu
exécuté. Les gestionnaires restent dans le JS statique autorisé par la CSP.

## Import et export

YAML Nova Atlas et OPML 2.0. OPML : DTD/entités externes interdites, XML strict sans
réseau, ≤2 048 nœuds, profondeur ≤10, document ≤1 Mio (JSON HTTP ≤2 Mio après auth/CSRF). L'attribut
optionnel enabled conserve les désactivés pour Nova Atlas ; les autres lecteurs
OPML peuvent l'ignorer. Les catégories vides sont exportées. Aucun fichier local
ni URL de téléchargement d'import n'est accepté : uniquement un contenu transmis.

Preview retourne additions, doublons existants, catégories, sources et fingerprint.
Une entrée invalide ou un doublon interne rejette **tout** le document avec erreur
sûre : pas d'import partiel ni de suppression des existants. Le rapport détaillé
ligne par ligne des entrées rejetées reste une amélioration future.
L'application exige apply=true, la version prévisualisée, fingerprint et le
`preview_token` signé renvoyé par le serveur (validité 10 min). Changer le texte
ou la version exige une nouvelle preview. Seuls les ajouts absents sont appliqués ;
les conflits sur URL/catégorie existante restent inchangés, même nom/statut différent.
Pas d'écrasement implicite des paramètres existants.

## Concurrence, News, sauvegarde et rollback

`config/feeds.yaml.lock` stable : flock LOCK_EX non bloquant avec attente bornée
à 5 s. Sous verrou : relecture stricte, contrôle de version, validation complète,
écriture 0600 de `feeds.yaml.bak`, puis temp privé, flush/fsync, os.replace et fsync
du répertoire. Ne jamais supprimer un lock pendant l'exploitation. Le même UID/GID
et stockage POSIX local du Sprint 02 sont requis ; pas de promesse NFS/SMB/multi-hôtes.
Les éditeurs externes ne respectant pas flock restent un risque opérateur : arrêter
les écritures web avant une édition manuelle. La sauvegarde conserve **une seule**
version précédente ; ajouter des sauvegardes opérateur pour une rétention plus longue.

Crash avant remplacement : original intact, temporaire non publié. Après rename :
version complète visible ; fsync répertoire vise la durabilité. Un échec d'I/O après
rename peut signaler une erreur alors que la nouvelle version est visible : relire
avant retry. Une interruption système matérielle ne remplace pas une sauvegarde.

News vérifie chemin, device/inode, taille et mtime_ns au prochain cycle. Une rotation
atomique se propage entre processus même si le mtime est préservé. Cycle déjà commencé
conserve son snapshot ; pas d'interruption brutale. Le journal des articles découverts,
les retries et checkpoints sont indépendants et préservés. L'URL exacte inchangée
garde ses validateurs. Changer une URL donne une nouvelle clé de cache sans effacer
l'ancien historique. Reset recharge la configuration, ne lève aucun embargo.

Rollback : drainer les mutations, sauvegarder config + deux états privés, conserver
les locks, revenir au code précédent. Les URLs/commentaires restent lisibles par
le chargeur historique ; noms/IDs sont alors ignorés. Pour restaurer feeds.yaml.bak,
travailler sur copie, valider avec SourceManager, puis remplacer atomiquement après
arrêt coordonné des producteurs/éditeurs ; ne jamais écraser le fichier automatiquement
au démarrage. Restaurer une ancienne liste peut perdre des ajouts récents : comparer
avant action. Ne pas restaurer un ancien état de santé pour contourner un embargo.

## PostgreSQL futur et limites

Le schéma de snapshots du Sprint 04 archive le texte YAML complet et les champs
historiques ; les nouveaux commentaires survivent à l'export. Il ne constitue pas
encore le modèle opérationnel Source Manager. Un futur cutover devra porter l'ID
stable de membership, enabled/position/name/added_at et la santé par URL distincte,
avec contraintes et transactions testées, plutôt qu'un dual-write non atomique.
Aucune migration SQL ni activation de PostgreSQL dans ce sprint.

Pas de découverte, canonicalisation globale, consolidation GUID, monitoring actif
à l'ouverture ou coordination distribuée. Les résolveurs DNS hérités peuvent survivre
à leur timeout dans la limite bornée décrite dans HTTP_SECURITY.md. Les quotas de
tests sont locaux au stockage partagé, pas une protection DDoS distribuée. Les
secrets historiques et données de production n'ont pas été consultés ou modifiés.
