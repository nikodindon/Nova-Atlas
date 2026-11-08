# Nova Atlas V2 — dossier RC-0, Sprint 13

Référence de départ : dev `66cb57d`, PR20 fusionnée après dix CI réussies.
Ce dossier prépare une candidature, **pas une mise en production ni un tag de
release**. Aucun déploiement utilisateur, aucune migration métier, aucun modèle
ou compte fournisseur réel utilisé. Le plan local non suivi est conservé.

## Décision Go / No-Go

**NO-GO pour annoncer une V2 qualifiée en exploitation publique ou multi-machine.**
**GO limité aux essais opérateur isolés**, avec données synthétiques et services
externes désactivés. Les CI vertes sont des preuves dans leur périmètre, pas une
qualification de tous les environnements.

| Porte | État / critère de sortie |
|---|---|
| P0 sécurité/intégrité connus | Correctifs 01A–01G conservés et régressions rejouées ; aucune faille exploitable nouvelle confirmée dans les parcours audités. Ce n'est pas un pentest exhaustif |
| Incident OpenRouter | Clé historique non réutilisée ; aucune candidate des motifs recherchés dans les blobs actifs. Révocation et revue d'usage **sans confirmation propriétaire** : restent obligatoires avant tout cloud |
| Restauration legacy | Comparaison intégrale des fichiers/modes/mtime/empreintes sur fixtures ; arrêt des producteurs exigé. Le smoke frontend redémarre le projet restauré neuf |
| PostgreSQL optionnel | pg_dump/pg_restore, rôles, Alembic et restart dans le smoke existant ; aucune autorité métier SQL ni migration implicite |
| Radio 24 h / disponibilité | Non effectuées. Courts essais synthétiques FFmpeg/Icecast et pannes ciblées seulement ; pas d'exactly-once ni disponibilité garantie |
| Deux machines | Hôte CPU observé : Ryzen 5 1600, environ 32 Go ; corpus CPU-only. Qualification complète A/B et machine B : **non effectuées** |
| Droits | Licence du projet absente à la racine ; droits de diffusion Edge/voix et catalogue musical non confirmés. Pas d'autorisation publique/commerciale déduite de la licence d'une bibliothèque |
| Sécurité des images OS | Versions/manifests contrôlés, patch PostgreSQL 18.6 rafraîchi. Scan CVE/SBOM complet des couches OS non effectué : obligatoire pour une distribution exposée |
| Capacité événementielle | Plafond explicite de 500 articles dans la fenêtre d'indexation ; corpus supérieur refusé, pas d'index partiel. Ne pas annoncer une capacité illimitée |
| Rétention radio | Archivage manuel prudent livré ; MP3/cache Android jamais effacés automatiquement. Capacité disque et maintenance opérateur à valider avant fonctionnement prolongé |

Un P0 ouvert de perte/corruption, auth, secret, SSRF, XSS ou exposition interne
bloque toute publication. Un P1 de restauration/restart/diffusion centrale cassée
bloque le profil concerné. Aucun de ces critères n'est reclassé P2 pour faire passer
une RC. L'absence de validation opérateur ou de droits constitue une porte non levée.

Validation locale : **1597 tests Python réussis** (baseline 1566, +31),
**33 tests frontend** et **10 scénarios Chromium natifs** réussis ; TypeScript, lint,
build, helpers CSRF, compilation Python, configuration Compose et diff vérifiés.
Les nouveaux smokes proxy/restauration/Icecast sont exécutés en CI ; le statut
final de la PR et ses artifacts font foi, pas un résultat déclaré avant exécution.

## Inventaire : ce qui est réellement opérationnel

| Composant | État et preuve | Limites / défaut |
|---|---|---|
| Flask, API Android, JSON/YAML | Opérationnel sur fixtures, client test + Chromium + Docker | Exploitation de l'installation utilisateur non testée |
| Sessions/auth/CSRF | Tests login, refus, logout confirmé, expiration, spoofing proxy | État mono-processus en mémoire ; restart Web impose une nouvelle connexion |
| CSP/XSS Flask/Svelte | Rendus hostiles inertes, nonce Kit, hash du style annonceur, tests navigateur | Pas une revue de toutes les extensions futures |
| Compose / nginx | Profils sélectifs, UID commun, read-only, ports loopback, smokes isolés | Reverse proxy et HTTPS opérateur à configurer, Host autorisés à filtrer sur le proxy d'entrée |
| SvelteKit | Opt-in, SSR public, audio racine, hub admin + consultations | Mutations/discovery/médias/flashs restent Flask ; rechargement à la frontière interrompt le lecteur Svelte |
| RSS/Atom, reprise | Parser/caches/429/identités et retries validés hors ligne | Aucune campagne de collecte Internet ni validation de disponibilité réelle des sources |
| Source Manager | YAML autoritaire, mutation versionnée/atomique et API testées | Aucun cutover PostgreSQL ; les tests d'URL réseau sont explicites et limités |
| Découverte | URL/site et adaptateur SearXNG optionnel sur mocks | Fournisseur absent accepté ; RSSHub reporté, aucun crawler généraliste |
| Événements | Index dérivé opt-in, corpus et overrides conservés | Rapprochement conservateur non fact-checking ; limite 500, multilingue limité |
| Images | Désactivées par défaut ; candidats/droits/approbation/miniatures testés | Une licence inconnue ne permet aucune publication. CC BY 4.0 seul sous-ensemble actuellement accepté ; aucun vrai média téléchargé pour cet audit |
| Exports statiques | Parcours toujours supporté ; staging média corrigé sous site/media | renameat2/exchange Linux exigé, échec fermé si indisponible |
| LiteLLM | Optionnel, aliases/local-only/fallback/erreurs validés via faux fournisseur | Aucun compte OpenRouter/Ollama Cloud utilisé, budget mémoire non financier strict |
| PostgreSQL/Alembic | Optionnel, snapshots import/export et restauration jetable | Moteurs legacy toujours autoritaires ; audio reste fichiers |
| Scheduler / verrou LLM | Créneaux/arrêt/races/DST/flock/crash couverts | POSIX local commun ; pas de verrou inter-machines ni rattrapage distribué garanti |
| Radio Studio | Désactivé par défaut, journal/outbox/reprise/reçus sur fixtures | Source FFmpeg, pipe, mount et auditeur restent observations distinctes |
| TTS | Edge historique inchangé ; laboratoire fixture/Piper préparé | Aucun moteur alternatif qualifié, aucun fallback payant implicite, aucune écoute de qualification effectuée |

## Défauts reproduits et correctifs ciblés

1. **Export média sous /app read-only** : mkdtemp utilisait le parent de site,
   extérieur au bind mount. Reproduction avant correction ; staging désormais
   `site/media/.media-export-*`, même filesystem que la destination. fsync des
   sous-répertoires puis échange atomique ; un échec conserve l'ancien arbre.
   Régressions : emplacement, échange en échec, images/attributions/révocations.
2. **Revalidation du manifeste par carte** : une page de deux articles validait deux
   fois tout le manifeste. `public_many` le valide une fois par page ; chaque image
   approuvée conserve les contrôles d'empreinte/octets, la route média revérifie.
3. **Coût de projection texte répété** : Coverage était aussi construit deux fois
   par article. Projection réutilisée, cache de deux versions SHA-256 maximum,
   lecture du fichier autoritaire à chaque requête, copies de dictionnaires.
   Fichier modifié/corrompu/vide détecté ; aucune décision de droits mise en cache.
4. **Quota Studio 1024 sans outil** : commande manuelle avec preview/version et leases.
   Archive privée puis tombstones puis compactage du journal. Une interruption
   après tombstones ne rend pas le créneau réadmissible. Données ambiguës, fichiers
   audio encore présents et dernier job observé par canal sont conservés.
5. **Restauration non démontrée** : outil de snapshots/restauration neuf et tests de
   refus d'état actif, liens, corruption, permissions, ENOSPC/interruption. Un
   sous-arbre illisible provoque un échec, jamais un snapshot incomplet « réussi ».

## Architecture supportée et limites matérielles

Cible de cette candidature : Linux x86_64, Python 3.12, Docker Engine/Compose,
stockage POSIX local (ext4/xfs/btrfs correctement configuré), UID/GID cohérents.
Aucun GPU requis pour Web/News/Radio/orchestration. Le fournisseur LLM est séparé.
Node24 LTS uniquement dans le frontend facultatif ou pour son développement.

Les manifests Node/nginx/PostgreSQL/Icecast contiennent des architectures ARM64 ;
**cela ne constitue pas une validation ARM64**. Docker rootless, user namespaces,
NFS/SMB/9p/virtiofs/FUSE et permissions réseau ne sont pas qualifiés. Les stockages
réseau refusés par les garde-fous ne deviennent pas supportés en retirant le test.
Les locks ne doivent jamais être effacés en fonctionnement. Une copie du stockage
vers un autre hôte ne partage plus les inodes : arrêter l'ancien groupe avant la
bascule ; flock ne protège pas un serveur LLM contre une autre machine indépendante.

## Installation neuve, sûre par défaut

Les commandes ci-dessous sont un **protocole opérateur**, pas des actions exécutées
sur l'installation. Consulter DOCKER_DEPLOYMENT.md et FRONTEND_V2.md pour TLS.

```sh
set -e
python3.12 -m venv .venv-operator
. .venv-operator/bin/activate
python -m pip install -r requirements-runtime.lock
# Parent exclusivement neuf, créé pour le compte de service non-root :
test ! -e /srv/atlas-rc
sudo install -d -m 0700 -o "$(id -u)" -g "$(id -g)" /srv/atlas-rc
python -m deploy.init --directory /srv/atlas-rc/state --admin-username VOTRE_IDENTIFIANT
# Mot de passe saisi au prompt ; aucun secret dans argv ou dans ce document.
python -m deploy.preflight --storage /srv/atlas-rc/state --inspect-data --compose
# Au besoin : --profiles frontend ou gateway,database après initialisation explicite.
docker compose --env-file /srv/atlas-rc/state/compose.env --profile web config --quiet
# Après accord opérateur seulement :
docker compose --env-file /srv/atlas-rc/state/compose.env --profile web up -d --build --wait
```

Secrets owner-only, parent privé, aucun mot de passe d'exemple utilisable par le
runtime container. Préflight en lecture seule : métadonnées de secrets uniquement,
aucune initialisation, correction, écriture probe, migration ou démarrage. Il ne
prouve pas la validité des valeurs de secrets ni la disponibilité d'un fournisseur.
Son `--compose` utilise /dev/null comme env file et des valeurs structurelles
contrôlées ; il ne remplace pas la validation privée du compose.env opérateur.
`pass`, `fail`, `warning`, `not_tested` sont distincts ; un ensemble d'avertissements
ne devient pas « prêt production ». Espace libre minimum : 4 Gio pour le contrôle,
prévoir davantage pour images, caches, sauvegardes et audio. Le disque local limité
n'a pas été utilisé pour construire une nouvelle stack durant ce sprint.

Activer SvelteKit avec l'overlay/profils `web,frontend`. Le port 5173 reste loopback,
Node/Flask sur frontend_api interne et gateway seul sur frontend_edge. Le schéma
public est déclaré par l'opérateur, les en-têtes clients sont remplacés, seuls les
pairs exacts de proxy sont autorisés. Filtrer Host et terminer HTTPS au proxy hôte.
Aucun CORS permissif, aucune exposition PostgreSQL/LiteLLM/Icecast ajouté par défaut.

News et Radio restent désactivés lors du premier démarrage. Le jeu feeds initial
est vide. Configurer un backend simulé dans un réseau jetable pour les essais ;
ne jamais pointer le protocole de test vers un compte cloud. En exploitation,
choisir ensuite explicitement fournisseur, modèle et sources ; ne pas lancer
`--profile full` tant que LLM, droits, stockage et radio ne sont pas qualifiés.

## Mise à niveau historique et retour arrière

Ne jamais copier par-dessus les données actives. Arrêter les admissions et tous
les producteurs, sauvegarder, vérifier/restaurer sur répertoire neuf puis comparer.
Conserver originaux, images/digests précédents, config et droits. Adapter seulement
les métadonnées de déploiement (`ATLAS_STORAGE`, UID/GID, ports, proxies) après la
vérification d'empreintes, et consigner cette différence intentionnelle.

Rollback frontend : arrêter frontend/gateway, remettre le reverse proxy vers Flask,
revenir à la composition de base. Rollback applicatif : arrêter proprement puis
revenir aux images/config compatibles, sans supprimer de volumes. Revenir à une
sauvegarde antérieure après de nouvelles publications perdrait ces nouveautés :
exporter/comparer avant tout choix opérateur. PostgreSQL n'est pas une autorité
métier ; si un futur cutover SQL écrit seul, export vérifié obligatoire avant retour
legacy. Aucun rollback « sans perte » n'est promis dans cette situation.

## Sauvegarde et restauration effectivement vérifiées

```sh
# Répertoires de sortie neufs, hors de l'installation, support privé.
python -m deploy.recovery snapshot --source /srv/atlas \
  --destination /sauvegardes/atlas-snapshot-neuf --include-secrets
# Preview : aucun secret ouvert, aucune écriture. Après arrêt vérifié :
python -m deploy.recovery snapshot --source /srv/atlas \
  --destination /sauvegardes/atlas-snapshot-neuf --include-secrets --stopped --apply
python -m deploy.recovery verify --source /sauvegardes/atlas-snapshot-neuf
python -m deploy.recovery restore --source /sauvegardes/atlas-snapshot-neuf \
  --destination /srv/atlas-restaure-neuf
# Après revue : même commande avec --apply, puis préflight et retargeting explicite.
```

La commande n'arrête aucun service. `--stopped` est une attestation opérateur,
complétée par l'acquisition non bloquante des leases connus ; ce n'est pas une
preuve qu'un writer externe non coopératif est arrêté. Les inventaires hashés avant
et après copie détectent les changements observables. Limites 100000 entrées et
32 Gio ; un dépassement refuse au lieu de tronquer. Destination nouvelle/disjointe,
symlinks/hardlinks/fichiers spéciaux refusés ; renameat2 NOREPLACE et fsync sur même
filesystem. Aucun fichier existant n'est écrasé, aucune donnée source effacée.

Couverture : configuration, compose.env, sources, articles/seen/retries/checkpoints,
états HTTP, index/overrides, manifeste et binaires images, cache LLM, MP3/reçus,
Studio et archives, cache Android, musique/fonds, exports et logs. Les modes et
mtime sont préservés ; le propriétaire restauré est l'UID qui effectue la restauration,
à mapper explicitement sur la destination. Le manifeste contient des empreintes,
pas les valeurs de secrets, mais il reste privé. Les snapshots contenant secrets
sont confidentiels : chiffrement/support privé et accès opérateur à prévoir.
L'empreinte détecte une corruption, pas l'authenticité d'une sauvegarde hostile.

Le compose.env restauré conserve son contenu d'origine : **le retargeting est
obligatoire avant toute commande Compose**, sous peine de viser l'ancien stockage.
Aucun service n'est lancé automatiquement par restore.

PGDATA brut n'est jamais copié avec cette commande. S'il existe, refus sauf
`--exclude-pgdata` explicite, en plus d'un pg_dump séparé, permissions 0600 et
restauration PostgreSQL neuve selon DATABASE_MIGRATION.md. Ne jamais assimiler
l'exclusion à une sauvegarde de la base. Dump/restauration/verify sont exercés dans
le smoke PostgreSQL ; une stratégie de point cohérent DB+fichiers reste opérateur.

Le smoke Docker de base restaure aussi les MP3 sinusoïdaux/reçus dans un répertoire
neuf puis les fait relire et décoder complètement par un conteneur network=none,
volume restauré read-only, même UID. Aucun audio synthétique n’est admis en production.

Preuve CI frontend : snapshot live refusé, arrêt Web/Node/nginx, copie vérifiée,
restauration dans un autre répertoire, modification du seul chemin synthétique
d'env, destruction du seul ancien projet de test, redémarrage du nouveau projet
sans rebuild, articles et octets d'image approuvée identiques. Aucun News/Radio réel.

## Matrice de pannes et portée des observations

| Panne | Preuve automatisée / observation attendue | Ce qui reste non qualifié |
|---|---|---|
| SIGTERM / restart Web | Smoke Docker, drain et inode conservé ; nouvelle session exigée | Proxy/HTTPS de l'installation |
| SIGKILL worker isolé | Tests multiprocessus flock et journaux, reprise OS | Panne physique/hyperviseur |
| Restart News | Contrats scheduler, cache RSS/retries après réinstanciation, role lease | Collecte réelle prolongée sur machine B |
| Restart Radio | Studio smoke FFmpeg : publication avant notification, réconciliation et Android | Programme quotidien en exploitation |
| Restart Icecast | Courtes sinusoïdes : HTTP mount + octets client + décodage avant/après restart CI | Reconnexion d'un programme 24 h et continuité auditeur |
| PostgreSQL perdu | Smoke DB : déconnexion/reconnexion, rollback, volume, dump/restore | Haute disponibilité, SQL autoritaire |
| LiteLLM/LLM indisponible, 429 | Faux providers, Retry-After, budgets, absence de fallback payant | Quotas réels / comportement d'un compte |
| RSS timeout/429/5xx/SSRF | DNS/transport simulés, autres sources poursuivies, embargo conservé | Disponibilité Internet réelle |
| MP3 interrompu/corrompu/reçu absent | Audio/Studio : aucun enqueue non validé, ancien Android conservé | Qualité humaine/latence réseau |
| JSON invalide / archive corrompue | Refus fermé et original préservé, diagnostic | Réparation opérateur automatique non fournie |
| Stockage plein / permissions | ENOSPC simulé, staging retiré, publication précédente intacte, préflight fail | Capacité réelle durable de l'hôte |
| Archive interrompue | Tombstones avant compactage, reprise idempotente sans réadmission | Rotation automatique volontairement absente |
| Restore après sauvegarde | Empreintes, modes, IDs, états/reçus et droits ; redémarrage neuf CI | Migration réelle multi-machine |

Les noms des tests et scénarios sont dans tests/test_rc_*.py, les suites des sprints
précédents et les modules deploy/*smoke.py. Une simulation n'est pas un incident
observé sur un service utilisateur. Aucun résultat « healthy » global n'est inféré.

## Diagnostic radio et protocole prolongé opérateur

```sh
python -m deploy.radio_diagnostic --data /srv/atlas/data --queue /srv/atlas/audio_queue
# Seulement sur une instance de test autorisée et une origine fixe sans credentials :
timeout 10s python -m deploy.radio_diagnostic --data /srv/atlas/data \
  --queue /srv/atlas/audio_queue --probe-mount http://127.0.0.1:PORT_TEST/nova
```

Les événements de lancement FFmpeg ne prouvent pas qu'un processus est encore
vivant ; l'âge de mesure reste visible. Octets écrits dans le pipe local, HTTP 200
sur mount, premiers octets reçus et décodage sont distincts. Le probe CLI utilise
un enfant sacrifiable limité à six secondes (DNS inclus), pas un streamer ; il
ne suit pas de redirection ni proxy ambiant, n'envoie aucun credential. Les octets
seuls ne prouvent ni complétude ni écoute. Aucun endpoint Web de probe arbitraire.

Pour qualifier 24 h sur projet jetable : réserver disque/CPU, générer uniquement des
sinusoïdes complètes avec reçus, musique de test dont les droits sont connus, jamais
Edge/LLM réel. Relever toutes les minutes les quatre observations, créneaux prévus,
admis/manqués, erreurs/reconnexions, silence de secours, dernier Android, croissance
journal/cache, CPU/RSS/disque et interruptions perçues par un client contrôlé.
Tester arrêt/reprise de chaque conteneur du **projet de test**, conserver ses logs
masqués et hashes. Arrêter si ressources insuffisantes, aucune purge automatique.
Ce protocole n'a pas été exécuté 24 h durant ce sprint.

## Rétention et archivage Studio

```sh
python -m deploy.studio_archive --data /srv/atlas/data --queue /srv/atlas/audio_queue \
  --before HORODATAGE_ISO_AVEC_FUSEAU
# Après sauvegarde vérifiée et arrêt Radio/génération : ajouter --version SHA_PREVIEW --apply
```

Dry-run par défaut ; cutoff antérieur d'au moins un jour ; version SHA et leases
runtime/génération/journal exigés à l'application. Seuls completed/skipped sans
reprise ni canal actif sont candidats. Dernier job par canal et toute publication
encore présente dans la queue/cache sont protégés, même corrompus. Aucun MP3 ou reçu
n'est supprimé. L'opérateur peut déplacer explicitement d'anciens actifs vérifiés
vers une sauvegarde privée avant compactage, en gardant les derniers Android et
les états ambigus sur place. Les nouvelles admissions consultent les tombstones.

Archives privées sous radio_studio/archives, index des IDs séparé, schéma journal 1
inchangé. Ordre durable : archive complète → index → retrait du journal. Un crash
peut laisser une copie double, jamais un créneau ancien rendu neuf. Préflight
inspecte les archives référencées (plafond 128 fichiers / 64 Mio de vérification),
index limité à 100000 IDs ; dépassement ou corruption bloque sans effacer.
Sauvegarder journal + index + archives ensemble. Ne pas revenir à une ancienne
version ignorant les tombstones tout en reprenant le scheduler sur cet état.

Cache Android, MP3, logs et archives n'ont pas de suppression automatique globale.
L'archivage ne promet pas de résoudre la croissance du disque ; la maintenance
manuelle et le futur stockage jobs doivent être planifiés. Quota 900 = warning,
1024 = fail au préflight. Ne jamais effacer un .lock ni vider le journal à la main.

## Frontend, sécurité et charge

Les 33 tests frontend et dix scénarios Chromium natifs sont conservés. Le smoke
vrai nginx/Flask/Node ajoute une séquence mobile publique puis desktop privée :
actualité, événements, archives/rapport, image locale et attribution, même audio DOM,
canal, login/logout, CSRF refusé puis ajout Source Manager avec version, observations
discovery/médias/radio, flash inconnu sans génération, Flask puis retour Svelte.
Le son navigateur est simulé : ce scénario ne mesure pas Icecast. Expiration serveur
reste testée par horloge simulée ; retrait de cookie n'est pas appelé expiration.
Les configurations/éditions/flashs/découverte complets restent Flask, sans seconde
authentification. Le lecteur ne survit pas au rechargement inter-applications.

Mesures CPU-only et résultats bruts : measurements/rc/README.md. Corpus : 240 sources,
1200 articles du jour + 1000 historiques, 240 événements sur sous-corpus borné,
100 candidats images, une illustration synthétique approuvée, 12 lectures/4 threads.
Pas de réseau RSS, modèle, TTS, GPU ou charge sur données utilisateur. 0,083 s moyenne
par page après cache, maximum froid 0,454 s ; 12 lectures en 1,11 s dans ce passage.
Ni SLA, ni test de saturation, ni mesure réseau/Node en production. Les bundles
restent environ 58,5 ko JS / 3,1 ko CSS gzip pour toutes les routes ; pas de score
Lighthouse. Accessibilité automatisée ciblée, contrôle manuel complet restant.

## Dépendances, images et licences

Audit daté du 8 novembre 2026 : npm conserve **4 avis faibles**, zéro moderate/high/
critical, liés à deepmerge 4.3.1 dans la fusion de configuration de build. Aucune
entrée utilisateur n'y est ajoutée par ce sprint ; pas de correction amont compatible
constatée, pas de downgrade Kit ancien. Override minimatch corrigé maintenu et testé.
Inventaire des métadonnées de licences dans measurements/rc/license-metadata.json,
pas un avis juridique ni une preuve que tous les notices/obligations de distribution
sont satisfaits. Un scan d'image CVE/SBOM complet reste à produire avant exposition.

| Image | Vérification et décision |
|---|---|
| Node24 bookworm slim @01d566e8… | Même digest que le registre ; branche LTS maintenue, pas de saut majeur |
| nginx1.30.6 alpine @583b7f2f… | Digest inchangé, tag rendu exact ; les correctifs publiés sont dans ce digest |
| PostgreSQL18.6 trixie @e3d24f69… | Rafraîchissement ciblé de l'image même version, ancien digest @3a426452… ; migrations/dump/restore en CI |
| LibreTime Icecast2.5.0 Debian @01b91f4d… | Digest toujours disponible et inchangé ; version amont courante, image tierce LibreTime, disponibilité ≠ scan sécurité |
| LiteLLM1.108.0 | Pas de mise à jour aveugle ; smoke du faux fournisseur conservé, DB facultative non activée |

Sources primaires : [Node](https://nodejs.org/en/about/previous-releases),
[nginx et correctifs](https://nginx.org/en/security_advisories.html),
[PostgreSQL](https://www.postgresql.org/support/versioning/),
[Icecast](https://icecast.org/download/) et [image LibreTime](https://hub.docker.com/r/libretime/icecast).
Les digests complets résident dans les Dockerfiles/Compose. Les paquets apt installés
au build ne sont pas tous verrouillés : ce n’est pas un build binaire identique garanti.
Conserver les images construites par ID/digest et leurs inventaires pour rollback,
pas seulement une recette à reconstruire plus tard. Un manifest ARM64 ne
suffit pas à qualifier cette architecture.

Pas de LICENSE racine trouvée : choix propriétaire à obtenir avant redistribution,
pas d'attribution MIT inventée ([règle de licence GitHub](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)).
Le [client Edge](https://github.com/rany2/edge-tts) et son code LGPL ne constituent
pas une autorisation de radio publique du service Microsoft. Ne pas transposer les
conditions Azure à cet accès Edge sans contrat applicable. Piper/Kokoro/poids/voix :
revue modèle exact et écoute humaine avant qualification ; aucun clonage/téléchargement.
Cloud : conditions du compte et quotas à valider, clés jamais dans le navigateur.
Images : obligations [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode)
conservées ; source publique ≠ licence, droits de tiers non garantis. Musiques/fonds :
aucun catalogue utilisateur inspecté, droits de diffusion non établis.

## Checklist restante et seconde machine

- Confirmer révocation/revue d'usage de la clé historique ; renouveler les secrets
  pour l'installation cible, jamais réutiliser la clé exposée.
- Statuer sur licence du projet, Edge/voix, musiques et redistribution des dépendances.
- Réaliser scan CVE/SBOM des images construites et revue de déploiement HTTPS/Host/proxies.
- Sur A puis B isolées : `python -m deploy.machine_report --label A --output rapport-A-neuf.json`
  (B sur l'autre machine), puis corpus RC, préflight, installation/restauration neuve
  et smokes autorisés. Le label est déclaré, ne prouve aucune qualification.
- Relever versions OS/Docker/Node/FFmpeg/locks, CPU/RSS/espace, durée, erreurs et empreintes.
  Ne pas copier les secrets entre les rapports ; ne jamais publier leurs valeurs.
- Effectuer le protocole radio prolongé, réseau indisponible, disque plein simulé,
  redémarrages, sauvegarde, restore et conservation Android ; aucun bulletin réel.
- TTS séparé : spec modèle/voix/licence, corpus fixe, CPU-only Piper, RTF et écoute
  aveugle ; Kokoro/GPU seulement avec installation/ressources autorisées et mesurées.
- Contrôle manuel clavier/lecteur d'écran/mobile, qualité des contenus éditoriaux et
  des attributions ; tester le rollback avec l'opérateur sur une copie.
- Valider les plafonds (500 événements en entrée, 2000 articles/jour API, 1024 jobs,
  cache/disque), la maintenance et le volume réel attendu avant toute promesse 24/7.

La validation multi-machine, la radio 24 h, les comptes cloud et l'écoute TTS restent
**non effectués**, même si tous les contrôles de cette PR sont verts.
