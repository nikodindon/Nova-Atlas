# PostgreSQL et snapshots historiques — Sprint 04

Socle dev@0d7f787, PR #11 fusionnée explicitement après ses six contrôles réussis.
Aucune donnée réelle importée/lue, aucun producteur ni déploiement existant démarré.

## Tranche livrée et autorité des données

**News, Web, Radio, éditions, rapports, posts et API Android utilisent toujours les
fichiers historiques.** Le mode opérationnel reste `legacy` par défaut. Les seules
fonctions SQL effectivement branchées sont les outils CLI d'import, lecture,
vérification et export de **snapshots**. Ils sont utilisables pour préparer/tester
une migration ; PostgreSQL n'est pas annoncé comme stockage métier actif.

Un snapshot importé est une archive indépendante, immuable via l'API fournie.
Il n'est jamais synchronisé automatiquement avec des producteurs JSON actifs.
Une nouvelle version des sources produit un nouveau snapshot explicite, sans
modifier l'ancien. Il n'y a donc pas de dual-write ou de seconde source de vérité
opérationnelle. `storage.mode: postgres` dans main.py est refusé (code 78) : une
bascule métier serait mensongère tant que le parcours collecte/checkpoint/publication
n'est pas connecté transactionnellement. `select_storage` sait lire un inventaire
legacy ou un snapshot PostgreSQL explicitement choisi, sans migration implicite.

Les transactions SQL protègent les imports, **pas encore** la génération/publification
métier. Le flock des collecteurs, les checkpoints du Sprint 01E, le scheduler,
le cache LLM et les reçus MP3 restent indispensables et inchangés. Les tests
historiques de deux collecteurs/LLM/audio sont conservés. Leur remplacement SQL,
les verrous de lignes de jobs et le cutover des consommateurs font une prochaine
PR dédiée. Aucun verrou POSIX n'est supprimé ici.

## Schéma Alembic v0001_snapshots

| Table | Données réellement stockées |
|---|---|
| import_runs | identifiant déterministe du snapshot, version source, manifeste des fichiers/empreintes, comptes, succès et timestamp UTC |
| legacy_documents | chemin relatif, type, SHA-256 brut/sémantique, document complet JSONB et texte UTF-8 source |
| sources | identifiant stable, URL flux/canonique, catégorie, nom, état, date d'import et entrée historique complète |
| articles | identifiant stable, hash historique, URL, titres, source/catégorie, résumé, dates brutes/statuts et timestamps UTC possibles, publication et champs inconnus |
| processing_attempts | hash/article éventuel, état, tentatives, motif, prochaine tentative, checkpoint et version ; record complet |
| editorial_outputs | rapport/édition/post, date éditoriale civile, contenu, statut et fichier de provenance ; champs inconnus |
| seen_entries | hash et date historique brute, y compris date future/invalide préservée |

Clés primaires composées snapshot/id : pas de mélange entre deux versions. Les
identifiants de sources dérivent de catégorie + URL (une même URL peut appartenir
à plusieurs catégories), ceux d'articles de date éditoriale + URL. Le même hash/URL
peut légitimement réapparaître un autre jour hors rétention : aucune unicité globale
sur URL. Unicité snapshot/date/hash, FK de tentatives vers l'article lorsqu'il est
non ambigu, contraintes de statut et tentatives >=0. Deux copies parfaitement
identiques sont comptées et restent dans le document ; une même identité avec des
champs contradictoires fait échouer l'import, sans arbitrage silencieux.

Les chaînes d'URL originales longues ne sont pas indexées directement en B-tree ;
les identifiants hashés stables couvrent leur identité sans dépasser les limites
d'index PostgreSQL. Schéma réservé `atlas`, version Alembic privée dans `atlas_meta`.
Pas de tables jobs/événements/programmes radio fictives, ni de binaires MP3 en SQL.
Les identifiants et métadonnées permettront une normalisation ultérieure explicite.

## Formats audités et pris en charge

Inventaire basé sur les producteurs Python et fixtures synthétiques, pas les
fichiers de production :

- `config/feeds.yaml` : mapping catégorie vers liste d'URL ; entrées mapping
  url/name/enabled et champs supplémentaires également archivées.
- `data/articles/YYYYMMDD_articles.json` : liste, schéma public conservé ; champs
  hash/timestamp/pub_date/title/title_original/link/source/category/summary, extras.
- `data/seen_hashes.json` : mapping hash vers date YYYYMMDD string ou entier.
- `data/article_processing.json` : version 1, records par hash, champs racine
  supplémentaires, statuts/reprises/checkpoints préparés.
- `data/reports/YYYYMMDD_report.md`, `data/editions/YYYYMMDD_matin|midi|soir.md`.
- `data/posts/YYYYMMDD_HHMM_posts.json` (liste) et `.txt` (dérivé lisible conservé).

Pas d'import de config.yaml, .env, caches, logs privés, musique ou fichiers audio.
Les .bak et fichiers sans extension reconnue ne sont pas un jeu métier importé ;
un fichier json/md/txt dans un des répertoires désignés mais avec un nom non supporté
est refusé, pas ignoré comme succès. Répertoires non standard : préparer une copie
explicite dans le layout attendu, jamais déplacer les originaux automatiquement.

JSON invalide/non fini, clés dupliquées JSON/YAML, version de journal inconnue,
statuts/records invalides, liens avec credentials, symlinks, hardlinks et fichiers
spéciaux sont refusés. Les alias YAML sont refusés pour éviter des expansions
non bornées : produire une copie dépliée et la vérifier avant import si nécessaire.
Bornes : 64 Mio/fichier, 256 Mio/jeu et 10 000 fichiers. Toutes les ouvertures source
parcourent les composants avec O_NOFOLLOW/openat ; aucun accès réseau au contenu.

Le texte source complet est conservé : commentaires YAML, précision numérique
JSON et champs non projetés restent récupérables. JSONB sert aux projections et
vérifications ; il ne remplace pas les originaux archivés. Les NUL et valeurs non
représentables sont refusés avant écriture. Les originaux sur disque restent intacts.

## Dates

Nouvelles created_at/updated_at/imported_at : UTC timezone-aware. published_at est
rempli uniquement si pub_date déclare un offset/fuseau ou un epoch explicite valide.
Le timestamp historique de génération sans offset n'est pas transformé en fetched_at
ou en UTC certain. first_seen_at peut venir de discovered_at epoch du journal quand
le hash désigne un article unique ; fetched_at reste NULL sans preuve. processed_at
n'est rempli que pour un timestamp explicitement timezone-aware.

Chaque valeur brute reste archivée ; statut aware, ambiguous_local, invalid ou
missing et UTC éventuel. Une heure répétée/inexistante sans offset reste ambiguë,
sans hypothèse Europe/Paris. Date éditoriale YYYYMMDD et noms des fichiers inchangés,
y compris minuit/année bissextile. Les dates seen invalides/futures sont préservées,
pas converties en autorisation d'effacer. Aucune réinterprétation des éditions/radio.

## Initialisation optionnelle Compose

Image officielle **postgres:18.6-trixie** épinglée par digest dans
`docker-compose.database.yml` (index amd64/arm64 entre autres ; CI amd64).
Version majeure figée : une future montée majeure exige pg_upgrade ou dump/restore,
pas un simple changement de tag sur le même PGDATA. PostgreSQL 18 utilise ici
`/var/lib/postgresql/18/docker` ; le montage parent `/var/lib/postgresql` est voulu.

Partir de l'état privé neuf du Sprint 02, puis :

```sh
.venv-container/bin/python -m pip install -r requirements-runtime.lock
.venv-container/bin/python -m deploy.database --directory deploy/local
```

Crée uniquement des chemins neufs : trois secrets 0400/parents privés, postgresql/,
migration_input/ et database_exports/ (0700). Refuse de remplacer un existant.
Aucun accès DB, import ou migration n'est exécuté par cette initialisation.

```sh
dc() {
  docker compose -f docker-compose.yml -f docker-compose.database.yml \
    --env-file deploy/local/compose.env --profile database --profile database-tools "$@"
}
dc config --quiet
dc build dbtools
dc up -d --wait postgres
```

Le Compose historique fonctionne sans overlay ni base. `database` seul n'active
ni News/Radio/Web ni LiteLLM. postgres/dbtools sont seuls sur le réseau database
**interne**, aucun port hôte. Postgres tourne sous le même UID/GID de service,
non-root, capacités retirées ; l'image officielle gère l'UID numérique via NSS
wrapper. Racine RO, sockets/tmp en tmpfs. Stockage DB bind **indépendant** des JSON :
`ATLAS_STORAGE/postgresql`, POSIX local, jamais NFS/SMB ou montage de data/ à la place.
L'image, le filesystem et les permissions arbitraires rootless/Desktop ne sont
pas validés hors CI Linux Engine ; ne pas réutiliser des données d'un autre cluster.

Ressources par défaut : 512 Mio, 1 CPU, 30 connexions, shared_buffers 64 Mio ; adapter
après mesure pour de gros imports. Pool SQLAlchemy 2, overflow 0, attente 5s,
connexion 5s, requête 15s/verrou 5s. Erreur/déconnexion annule la transaction ;
pas de retry infini ou reprise partielle annoncée réussie. Arrêt SIGINT PostgreSQL
(fast shutdown propre), délai 2 min configurable ATLAS_DB_STOP_GRACE. Une expiration
Docker peut tuer le processus ; PostgreSQL rejoue son WAL au restart, sans remplacer
les sauvegardes. Health pg_isready = disponibilité serveur, pas schéma ni réussite
métier. dbtools health vérifie une connexion authentifiée SELECT 1 séparément.

## Secrets et rôles

atlas_admin superuser bootstrap/backup seulement, secret monté seulement dans le
conteneur DB. atlas_migrator non-superuser/non-createdb/non-createrole, propriétaire
atlas/atlas_meta et DDL. atlas_app non-superuser/non-createdb/non-createrole : CONNECT,
USAGE atlas et SELECT/INSERT sur ses tables, aucun UPDATE/DELETE/CREATE ni accès aux
versions Alembic. dbtools monte app+migrator pour les commandes explicitement confiées
à l'opérateur ; aucune application métier ne reçoit de clé DB pour l'instant.

Host TCP SCRAM-SHA-256, pas de trust réseau. Socket Unix local de bootstrap de
l'image officielle dans le conteneur de confiance ; exec Docker dans ce conteneur
est un privilège administrateur, pas une frontière d'auth utilisateur. Révoquer les
accès PUBLIC aux DB/schema de l'installation ; aucune API DB publique.

Aucun DATABASE_URL secret dans Compose/config, commande ou diagnostic : URL SQLAlchemy
construite depuis fichiers au runtime, paramètres masqués et erreurs CLI génériques.
Pas de SQL/valeur de credential dans les logs d'erreur DB. Changer un fichier secret
**ne change pas** le mot de passe du rôle existant : rotation ALTER ROLE hors logs
par procédure DBA puis recréer les consommateurs ; init ne s'applique qu'au cluster
neuf. Hors du bridge local de confiance, prévoir TLS verify-full et CA validée dans
une prochaine configuration, ne pas exposer ce déploiement comme serveur distant.

LiteLLM reste sans DB : aucun DATABASE_URL ou droit ajouté à son service. Une future
base LiteLLM devra être distincte avec ses propres rôles/schéma/migrations.

## Alembic explicite

```sh
dc run --rm dbtools migrate head
# Vérification authentifiée, sans collecteur :
dc run --rm dbtools health
# Développement : rendu SQL sans connexion ni secret
.venv-container/bin/python -m alembic upgrade head --sql
```

Pas de create_all de production, pas de migration au startup. Les DDL sont figés
dans migrations/versions/0001_historical_snapshots.py. Les futures révisions seront
revues et testées upgrade/downgrade sur une base jetable. `migrate base --allow-drop`
est **destructif SQL**, réservé aux tests ou à une procédure DBA après sauvegarde ;
ce n'est pas la procédure normale de rollback vers legacy.

## Import, dry-run, reprise et export

Arrêter/drainer tous les producteurs concernés et copier le jeu historique dans
migration_input/ avec ses sous-répertoires config/ et data/. L'outil ne fait pas cette
copie et ne manipule pas les originaux. ATLAS_IMPORT_ROOT peut pointer explicitement
une **copie** préparée, montée RO dans /imports. Recommandation : travailler sur
copie privée hors du checkout, pas monter un home ou l'installation entière.

```sh
umask 077
dc run --rm dbtools import --root /imports > import-review.json
# Examiner files/empreintes/comptes et l'identifiant snapshot du rapport.
dc run --rm dbtools import --root /imports --apply \
  --expect-snapshot SHA256_EXAMINE --quiesced
dc run --rm dbtools verify --snapshot SHA256_EXAMINE
dc run --rm dbtools export --snapshot SHA256_EXAMINE --destination /exports/test-roundtrip
```

Sans --apply : lecture seule, aucun engine/connexion DB créé. --apply exige le hash
examiné et --quiesced (déclaration de l'opérateur, pas un arrêt automatique).
L'inventaire et les SHA-256 sont revérifiés avant écriture et avant commit. Ce n'est
pas un snapshot atomique de producteurs concurrents : un changement complet entre
observations peut échapper à la détection ; l'arrêt coordonné et la copie restent
indispensables. Aucune simple option ne prouve l'arrêt d'une autre machine.

Un snapshot = une transaction, sérialisée par advisory_xact_lock de son identifiant.
Deux importeurs identiques donnent un seul historique/jeu ; le second vérifie puis
répond already_imported. Un crash/une exception annule tout, puis relance complète
sûre. Un succès n'est visible qu'au commit. Les échecs/dry-run sont dans le rapport
CLI (à conserver privé), pas dans une ligne DB prétendument réussie. Un fichier
corrompu n'efface jamais un import précédent. Les gros imports peuvent atteindre
les timeouts/limites mémoire et doivent être testés sur copie avant réglage raisonné.

La vérification compare manifeste, texte brut, empreintes, identifiants, tous les
champs projetés et les comptes. L'export relit une transaction repeatable-read/read-only,
recrée un répertoire privé temporaire, vérifie l'équivalence sémantique, puis publie
le **répertoire complet** par renameat2 NOREPLACE sur le même filesystem. Destination
neuve obligatoire, symlinks parents refusés ; échec nettoyé, aucun original écrasé.
SNAPSHOT_REPORT.json contient comptes/empreintes. Le texte UTF-8 source est archivé
et repris par cet export ; le contrat principal reste l'équivalence sémantique,
pas une promesse générale de reproduire toutes les métadonnées/permissions/encodages
historiques. Réimporter l'export comme nouvelle copie et comparer les rapports.

## Sauvegarde, restauration et rollback

Sauvegarde logique cohérente (fichier privé sur l'hôte, pas dans les logs) :

```sh
umask 077
dc exec -T postgres pg_dump -U atlas_admin -d nova_atlas -Fc > nova-atlas.dump
```

Sauvegarder séparément config/secrets et audio_queue/reçus/cache Android/musique,
ainsi que les originaux JSON/YAML. pg_dump ne sauvegarde pas les MP3 ni les mots de
passe de rôles globaux. Initialiser les mêmes rôles et leurs nouveaux credentials
dans une installation **neuve**, créer la DB cible puis pg_restore --exit-on-error.
Tester sur une DB neuve, jamais purger l'existante pour essayer un restore :

```sh
dc exec -T postgres createdb -U atlas_admin nova_restore
dc exec -T postgres pg_restore -U atlas_admin --exit-on-error --dbname nova_restore < nova-atlas.dump
# Vérifier chaque snapshot avec la DB restaurée explicitement sélectionnée :
dc run --rm -e ATLAS_DB_NAME=nova_restore dbtools verify --snapshot SHA256_EXAMINE
```

Ne pas utiliser --no-owner/--no-acl sans réévaluer les droits et la procédure de
vérification. Conserver digest image, référence Git, version Alembic, comptes,
manifestes et résultat du restore. Un cluster physique brut doit être sauvegardé
avec une méthode PostgreSQL cohérente ou arrêté ; ne pas copier PGDATA à chaud
comme une simple arborescence de fichiers.

**Rollback actuel** : les producteurs ne quittent jamais legacy dans cette PR ;
drainer les outils, arrêter le profil DB, reprendre le Compose historique avec les
originaux intacts. Conserver les snapshots et dumps, ne pas supprimer postgresql/.
Les nouveaux articles produits en legacy après un import ne sont pas dans ce vieux
snapshot : ne pas les écraser avec un ancien export.

**Futur cutover SQL** : arrêter les producteurs, exporter/vérifier les publications
SQL créées après bascule, restaurer dans une copie legacy neuve, vérifier API/édition/
radio puis basculer ensemble. Sans export de ces nouvelles écritures, le retour
aux anciens fichiers **perdrait des données**. Aucune promesse de rollback sans
perte tant que ce protocole métier complet n'est pas implémenté et testé.

## Validation et limites

Unit tests hors réseau avec fichiers temporaires et SQLite pour les transactions
élémentaires ; SQLite n'est ni le backend de production ni une preuve des droits/
verrous PostgreSQL. CI dédiée : vraie image PostgreSQL jetable, Alembic/schema,
rôles, imports répétés/concurrents, crash avant commit, checkpoint importé, export,
déconnexion/restart, pg_dump/pg_restore, downgrade/upgrade. Les smokes Docker/LiteLLM
existants et Chromium restent obligatoires. Aucun vrai RSS, LLM, TTS, Icecast ou
streamer lancé par le nouveau job DB. Les futures transitions de traitement SQL,
publication métier, jobs et coordination multi-hôtes restent à livrer.

Références : [image officielle](https://github.com/docker-library/docs/tree/master/postgres),
[Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html),
[pool SQLAlchemy](https://docs.sqlalchemy.org/en/20/core/pooling.html),
[pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html).
