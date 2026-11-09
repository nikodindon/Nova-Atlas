# Qualification terrain — Sprint 15, RC-1 conditionnelle

## Décision actuelle

**NO-GO distribution publique. RC-1 non qualifiée.** Le dossier autorise seulement
la préparation d'essais privés, chacun soumis à l'autorisation de l'opérateur.
Aucun déploiement sur les machines A/B, smartphone physique, test de 24 h ou écoute
humaine n'a été exécuté par ce sprint. Une CI verte n'est pas une permission de
lancer Docker sur un hôte utilisateur ni une attestation de droits de diffusion.

Références dev de départ : serveur `0e8752622bbbe16cf9b3b187bb7d69a9b1e4533b`
(PR21 fusionnée) ; Android `0bf4ba0dcbedf62521ae6daebaca9b703e7c1354`
(PR1 fusionnée après trois CI réussies). Branches `feature/v2-field-qualification`.
PR serveur [#22](https://github.com/nikodindon/Nova-Atlas/pull/22), Android
[#2](https://github.com/nikodindon/nova-atlas-android/pull/2). Les SHA effectivement
compilés et scannés sont inscrits dans leurs artifacts ; une mise à jour du code
impose de conserver la nouvelle référence, pas de recycler un PASS ancien.
Le plan non suivi du dépôt serveur demeure inchangé.

## Portes et preuves

Le modèle versionné est `docs/measurements/field/qualification-initial.json`.
Il s'agit d'un **état initial**, pas du résultat final des CI. Toutes les portes
matérielles restent NOT_TESTED ; droits, vulnérabilités, secrets et distribution
sont BLOCKED jusqu'à leurs revues. Les résultats CI courants sont séparés des
qualifications d'exploitation.

| Porte | État terrain | Critère de sortie |
|---|---|---|
| Code/CI | WARNING dans le registre initial ; preuves en PR | Toutes les suites et nouveaux tests réussis sur les SHA retenus |
| Installation A | NOT_TESTED | Installation neuve/permissions/arrêts/restauration, preuve physique A |
| Installation B | NOT_TESTED | Même campagne, mesures propres à B, sans extrapoler A |
| Frontend Flask/SvelteKit | NOT_TESTED terrain | HTTPS de test, navigation/mobile, CSP, auth/CSRF, frontières legacy |
| RSS | NOT_TESTED terrain | Serveur de fixtures contrôlé, 200/304/429, retries et absence de source réelle |
| Événements/images | NOT_TESTED terrain | Fixtures/provenance/attribution/révocation, articles inchangés |
| Sauvegarde/restauration | NOT_TESTED terrain | Retour dans un projet neuf après usage, empreintes/modes/identités comparés |
| Radio courte | NOT_TESTED terrain | Deux mounts de test, réception et décodage client ; CI distincte |
| Radio 24 h | NOT_TESTED | Autorisation, ressources réservées, 24 h observées, redémarrage et rapport de trous |
| Android émulateur | WARNING, CI versionnée | Tests CI reproductibles ; ne qualifie ni réseau mobile ni téléphone |
| Android appareils 1/2/3 | NOT_TESTED chacun | Campagne physique par appareil/API/APK, compte rendu individuel |
| TTS réel | NOT_TESTED | Moteur/voix/poids autorisés, RTF réel et écoute humaine |
| Licences | BLOCKED | Droits racine, voix/musiques/images/code/redistribution clarifiés |
| Vulnérabilités | BLOCKED | Inventaires/scans et triage des correspondances restantes |
| Secrets | BLOCKED | Confirmation opérateur de rotation/révocation historique et revue des déploiements |
| Distribution publique | BLOCKED | Revue humaine de toutes les portes obligatoires ; pas une simple CI verte |

Statuts : PASS, FAIL, WARNING, NOT_TESTED, BLOCKED. Pour chaque preuve : commit,
environnement, type fixture/ci/emulator/physical/operator, commande ou protocole,
date aware, résultat, limites, référence d'artifact/rapport privé et SHA256.
Le registre vérifie le schéma et la cohérence de ces affirmations ; il **ne peut
pas prouver la véracité d'une attestation humaine**. Ne jamais remplir « physical »
sur la seule base d'un émulateur ou d'un inventaire CPU.

```sh
umask 077
python -m deploy.qualification init --server COMMIT_SERVEUR_40_HEX \
  --android COMMIT_ANDROID_40_HEX --output /chemin/prive/qualification-v1.json
python -m deploy.qualification assess --report /chemin/prive/qualification-v1.json
# Après un essai réellement réalisé : evidence.json contient les neuf champs ci-dessus.
python -m deploy.qualification record --report /chemin/prive/qualification-v1.json \
  --expected SHA256_DU_RAPPORT --gate restore --status NOT_TESTED \
  --evidence /chemin/prive/evidence.json --output /chemin/prive/qualification-v2.json
```

Les sorties sont créées exclusivement, mode 0600 ; aucune version précédente n'est
écrasée. Un PASS exige une preuve de succès de même commit. Les portes A/B/appareils
exigent le type physical ; émulateur exige emulator ; droits/secrets/distribution
exigent operator. Les résumés n'affichent pas les textes libres ni les chemins privés.
Même tous les PASS donnent READY_FOR_OPERATOR_REVIEW, jamais une autorisation de
publication. Un FAIL enregistré bloque l'automatisation des essais privés ; une
exception doit être examinée humainement, pas masquée en NOT_TESTED.

## Installation neuve : opérateur uniquement

Avant toute commande Docker sur A ou B : confirmer hôte, fenêtre, ressources,
ports libres, sauvegarde de l'installation existante et absence de volumes communs.
Profil A attendu Ryzen5 1600AF/32 Gio/1650Super4 Gio ; B i5-8300H/16 Gio/1050mobile4 Gio.
Un label A/B dans un fichier n'est pas une vérification du matériel. GPU non requis,
non qualifié sans expérience dédiée. ARM64, rootless et stockage NFS/SMB restent
non validés ; flock/renommages atomiques exigent POSIX local partagé.

L'hôte observé durant ce sprint a seulement fourni six échantillons en lecture
seule sur environ cinq secondes. Libre minimum : environ **1,94 milliard d'octets**,
sous le seuil conservateur de 4 Gio. Aucune stack locale n'a été lancée ; aucune
mesure de performance Nova Atlas ni qualification A n'en est déduite. Rapport :
`docs/measurements/field/host-observation.json`. Ne pas libérer de données utilisateur
automatiquement pour contourner cette porte.

Après autorisation explicite, préparer un compte/UID/GID de service et un répertoire
parent neuf, privé, appartenant à ce compte. Depuis le checkout serveur retenu :

```sh
umask 077
ATLAS_FIELD_STATE=/srv/atlas-field-a/state-20261109
ATLAS_FIELD_PROJECT=atlas-field-a-20261109
python -m deploy.init --directory "$ATLAS_FIELD_STATE" --admin-username field-admin
python -m deploy.machine_report --label A --output /chemin/prive/machine-a.json
```

Le mot de passe est saisi de façon interactive, jamais dans argv ou une capture.
`deploy.init` refuse un état déjà présent, génère des secrets neufs, LLM non configuré
et feeds vides. Choisir dans le compose.env **neuf**, via un éditeur privé,
ATLAS_WEB_PORT=15055, ATLAS_FRONTEND_PORT=15173 et vérifier tous les chemins/UID/GID.
Ces ports sont des propositions à contrôler, pas une garantie qu'ils sont libres.
Ne pas lire/copier le .env ou les secrets de l'installation réelle.

```sh
python -m deploy.preflight --storage "$ATLAS_FIELD_STATE" --inspect-data --compose
# Après PASS des vérifications obligatoires et revue des WARNING/NOT_TESTED :
docker compose -p "$ATLAS_FIELD_PROJECT" --env-file "$ATLAS_FIELD_STATE/compose.env" \
  --profile web config --quiet
docker compose -p "$ATLAS_FIELD_PROJECT" --env-file "$ATLAS_FIELD_STATE/compose.env" \
  --profile web build web
docker compose -p "$ATLAS_FIELD_PROJECT" --env-file "$ATLAS_FIELD_STATE/compose.env" \
  --profile web up -d --wait web
```

Pour SvelteKit ajouter `-f docker-compose.yml -f docker-compose.frontend.yml`
et `--profile frontend`, puis build/up des services frontend et frontend-proxy
avec web. Conserver **le même -p, env-file, overlays et profils** dans les commandes
ultérieures. Lire DOCKER_DEPLOYMENT.md pour les secrets, réseaux et délais d'arrêt.
Ne jamais faire `--profile full up` comme raccourci : cela active des moteurs réels.
Pour démarrer Icecast seul : `--profile radio up -d --wait icecast`, sans lancer
le service radio ni News. Aucun modèle, TTS ou source Internet implicite.

## HTTPS et smartphones

Conserver les ports hôte sur loopback. L'opérateur autorise explicitement une
origine HTTPS de test via nginx/Tailscale avec certificat système valide et contrôle
réseau limité aux appareils de test. Aucun domaine public existant ne doit être
réaffecté. Vérifier Host, proxies autorisés, X-Forwarded-* réécrits et cookies Secure
comme décrit dans WEB_SECURITY.md. Pas de CORS permissif ni certificat trust-all.

Compiler l'APK debug avec ATLAS_ENV=isolated, ATLAS_ORIGIN et ATLAS_RADIO HTTPS
explicites de ce serveur ; conserver commit, SHA256 APK, signature et version.
Ads/Premium restent désactivés. Pour une future release, API **et radio** ne doivent
pas employer un domaine réservé/fictif ; la vérification tourne avant accès aux
credentials. Elle ne constitue pas une autorisation d'utiliser le domaine choisi.

Contrôler d'abord depuis le navigateur du téléphone `/api/public/site`, puis les
articles, événements et `/stream/nova-android` autorisés. Ne pas enregistrer l'URL
avec des secrets. Les routes administratives, médias candidats et credentials
Icecast ne sont jamais consommés par le client Android.

## Mesures et limites

Les instruments sont des outils du checkout opérateur, exclus de l'image runtime.
Ils ne contrôlent ni ne démarrent Docker, ne lisent aucun secret ou contenu de données.
Les sondes réseau sont explicitement déclenchées, hors routes Flask et sans changement
de la politique SSRF des RSS/articles/images.

```sh
python -m deploy.field_metrics --storage "$ATLAS_FIELD_STATE" \
  --output /chemin/prive/metrics-a.jsonl --duration 60 --interval 5 \
  --watch-file "$ATLAS_FIELD_STATE/data/radio_studio/journal.json"
```

Adapter le fichier surveillé au nom réellement présent ; un absent est NOT_TESTED.
Au plus 16 fichiers, **stat uniquement**, jamais le contenu ; les chemins ne sont
pas exportés, les slots correspondent à l'ordre des paramètres. Au plus huit PID
explicitement choisis et appartenant à l'utilisateur courant (`--pid NUMERO`) :
CPU ticks, RSS échantillonnée, état et naissance ; aucune cmdline/environ. Les PID
réutilisés ne sont pas pris pour la continuation de l'ancien processus. Les PID
conteneur d'un autre UID demandent un outil opérateur approprié : pas de privilège
élevé automatique. Une présence /proc n'est pas un healthcheck métier.

CPU hôte calculé entre échantillons ; mémoire hôte disponible, espace filesystem
libre et tailles de journaux observées. Ne pas présenter la RSS échantillonnée comme
un pic garanti, le disque global comme la taille exclusive d'Atlas ou ces valeurs
comme un benchmark applicatif. Pas de lecture des données privées ni de suppression.
Arrêt SIGINT/SIGTERM coopératif, quotas 32 Mio, nouveaux fichiers 0600 uniquement.
Une interruption/ENOSPC laisse le rapport partiel ; jamais un PASS implicite.

## Radio courte et campagne 24 h

Dans le fichier privé mounts.json, deux clés possibles primary/android contiennent
les URLs finales fixes de test. HTTPS système obligatoire ; exception HTTP **seulement
pour une IP littérale loopback avec --allow-loopback-http**. Pas de credentials,
query, fragment, redirects ou paramètres de proxy environnementaux. Ne jamais
assouplir external.py pour une radio ou un outil de terrain.

```sh
python -m deploy.field_radio --mounts-file /chemin/prive/mounts.json \
  --output /chemin/prive/radio-courte.jsonl
python -m deploy.field_report --report /chemin/prive/radio-courte.jsonl
```

Un worker borné à huit secondes englobe DNS/headers/lecture/décodage ; timeouts
socket 2/3 s, sample maximum 8 Kio habituel (plafond 64 Kio), FFmpeg local en demuxer MP3 imposé (pas de playlist auto-détectée),
protocoles file/pipe, timeout trois secondes. Le groupe dédié du worker est tué
en dépassement du budget, y compris son décodeur, jamais un streamer. Ce décodage porte sur l'échantillon,
**pas sur un MP3 complet ni sur le reçu du bulletin**. Mount200, octets, décodage,
pipe local et auditeur restent des observations distinctes. 429 respecte son
Retry-After ; les délais dépassant 24 h sont marqués au-delà de la campagne et
bloquent ses essais suivants. Embargo en mémoire de ce collecteur, pas un quota
global persistant entre deux lancements opérateur.

Avant la campagne longue : source synthétique complète autorisée, volumes isolés,
réception courte réussie sur les deux canaux, alertes disque/RAM et réservation
explicite de l'hôte. Ne pas activer Edge TTS ou un modèle pour produire les sons.
Utiliser les générateurs FFmpeg et le parcours avec reçus démontrés par
`deploy/radio_studio_smoke.py` et `deploy/radio_probe_smoke.py` comme recette auditée ;
ces scripts sont **CI-only** et ne doivent pas être débridés avec une fausse variable
GITHUB_ACTIONS sur un PC utilisateur. L'opérateur doit préparer sa source de test
avec le calendrier et l'autorisation correspondants ; aucune source longue n'est
lancée automatiquement par les outils de collecte. Cette admission/source longue
reste une porte opérateur non levée, pas un déploiement livré et déjà qualifié.

Après autorisation écrite et ressources réservées seulement :

```sh
python -m deploy.field_radio --mounts-file /chemin/prive/mounts.json \
  --output /chemin/prive/radio-24h.jsonl --duration 86400 --interval 60 \
  --authorize-long-run --resources-reserved
python -m deploy.field_metrics --storage "$ATLAS_FIELD_STATE" \
  --output /chemin/prive/metrics-24h.jsonl --duration 86400 --interval 60 \
  --authorize-long-run --resources-reserved
```

Les deux flags sont une attestation de l'opérateur, pas une preuve de réservation.
Ne pas exécuter ces commandes dans ce sprint sans autorisation. Elles ne démarrent
aucun producteur. Contrôler au début, après 1 h, avant/après un redémarrage Icecast
**de ce projet uniquement**, après 12 h et à 24 h : files, jobs, hash/reçu, cache
Android, journaux, métriques et réception décodée. Sauvegarder les horodatages du
redémarrage. Ajouter SIGTERM puis redémarrage d'un worker de test, et SIGKILL
uniquement d'un worker identifié appartenant à ce projet ; jamais un PID arbitraire.

`field_report` calcule les fenêtres entre première observation d'échec et première
observation rétablie, les trous d'échantillonnage et sommes d'octets. Ces fenêtres
ne sont **pas** des durées exactes de silence. Pas d'exactly-once Icecast, de mesure
d'auditeurs ou de disponibilité 100 %. Même un rapport couvrant 24 h ne transforme
pas la porte en PASS sans examen du restart, des interruptions et des critères.

## Matrice smartphones

Créer trois rapports privés distincts, sans ANDROID_ID, numéro de série, SSID,
compte Google ou IP personnelle dans un artifact public. Chaque formulaire indique
alias appareil, modèle/API, commit/SHA APK, version/signature, connexion, date,
protocole, résultat et limites. Distinguer toujours physical/emulator/JVM.

Pour chacun : installation, lancement chronométré, articles, catégories personnalisées,
Unicode/RTL, événements, attribution/révocation images, navigation, mode sombre,
rotation répétée, fermeture/recréation, arrière-plan/premier plan, sans réseau,
API500/429/timeout/JSON invalide sur serveur de test. Vérifier qu'une requête unique
reste active et que le polling cesse hors écran ; mesurer les octets réseau sur
une fenêtre annoncée avant de comparer l'intervalle 60 s, sans extrapolation batterie.

Radio : Play/Pause, buffering, écran verrouillé, veille, notifications et arrêt
système, casque/Bluetooth, audio focus/appel, Wi-Fi→mobile, perte/retour réseau,
mount404/503, restart Icecast, EOF, TLS invalide refusé. Pause doit annuler toute
reprise. Consigner consommation mémoire/batterie, durée et méthode réellement
observées ; laisser NOT_TESTED si indisponible.

En debug, « Exporter les observations radio » utilise le sélecteur de document
Android : export local et explicite. Anneau de 128 événements, compteurs et octets,
horloge monotone ; ni URL, exception, identifiant personnel, SSID ou secret. Les
événements distinguent intent UI/session, buffering, ouverture réseau, premiers
octets, lecture locale, Pause, reprise, erreur terminale et EOF. Les octets réseau
ne prouvent pas le décodage ; isPlaying ne prouve pas l'écoute. Données perdues au
redémarrage du processus, pas un journal durable de diffusion.

## Sauvegarde après usage et restauration

Sur le projet isolé, arrêter d'abord les producteurs (source synthétique comprise),
puis le projet avec son nom/env/profils explicites. Conserver les fichiers .lock.
Un snapshot de données vivantes n'est pas cohérent simplement parce que copie/fsync
réussissent. Avec PostgreSQL, pg_dump cohérent séparé et restauration dans une base
neuve, jamais une copie de PGDATA vivant. La DB reste non autoritaire.

```sh
python -m deploy.recovery snapshot --source "$ATLAS_FIELD_STATE" \
  --destination /chemin/prive/snapshot-field
# Puis seulement après revue du dry-run et producteurs arrêtés :
python -m deploy.recovery snapshot --source "$ATLAS_FIELD_STATE" \
  --destination /chemin/prive/snapshot-field --apply --stopped --include-secrets
python -m deploy.recovery verify --source /chemin/prive/snapshot-field
python -m deploy.recovery restore --source /chemin/prive/snapshot-field \
  --destination /srv/atlas-field-a/restored-neuf --apply
```

Le dry-run ne crée pas la destination. Les secrets de test font partie du snapshot
privé explicitement autorisé, jamais de Git/artifacts publics. Retargeter compose.env
restauré avant tout démarrage : nouveau chemin, nom de projet, ports, UID/GID et
proxy. Comparer empreintes/modes/mtime, nombres/identifiants des articles, catégories,
événements, droits/miniatures, MP3/reçus/jobs et cache Android ; puis autoriser web
seul et les services synthétiques choisis. Ne jamais présenter une sauvegarde
comme valide sans restauration réussie. Ne pas utiliser down -v sur des données
utilisateur. Aucun déplacement/suppression automatique de la production.

## Inventaire de sécurité et triage

Scans reproductibles CI : Syft1.55.0 et Grype0.121.2, assets officiels SHA256
vérifiés avant exécution. Chaque cible produit Syft JSON, CycloneDX JSON, Grype JSON,
version des outils, identité/digest de l'image, date/schéma de la base et empreintes.
Les artifacts ont une rétention de 14 jours : l'opérateur doit les conserver dans
un stockage privé durable avec leurs SHA, pas supposer un lien Actions éternel.

Six cibles : image finale application (Python/paquets natifs/FFmpeg), Node/SvelteKit,
nginx, Icecast, LiteLLM, PostgreSQL épinglé. Aucune stack/service démarré par le scan.
Les modules JS incorporés dans un bundle ne sont pas tous identifiables : le graphe
npm verrouillé et l'audit frontend existant restent des preuves complémentaires,
**pas une correspondance parfaite entre dépendances de build et octets distribués**.
La couverture native/bundles non identifiée demeure à examiner, jamais « propre ».

Android : tâche companionSbom sur debugRuntimeClasspath réellement résolu, SHA256
JAR/AAR, inventaire des .so réellement présents dans l'APK, hash APK et commit.
Grype scanne ce graphe transitif complet, SDK empaquetés désactivés compris. Cela ne
prouve pas leur atteignabilité ; des .so sans version identifiable restent
NOT_ESTABLISHED. Pas d'audit limité à six dépendances directes présenté comme global.

Le premier scan Android a trouvé Guava33.0.0-android / GHSA-xxph-c9ww-hj94 : version
vulnérable confirmée, exploitation runtime non établie. Fix ciblé33.7.2-android,
base Grype v6.1.10 datée du 8 novembre ; premier rescan : zéro correspondance dans
ce périmètre, 46 tests JVM et tests émulateur réussis. Sévérité High du scanner,
Moderate du fournisseur : conserver les deux, pas une fausse certitude.

Le premier inventaire images a aussi révélé Python3.12.14 affecté et zlib1.3.2-r1
sur nginx. Correctifs limités : base officielle Python3.12.16 au digest vérifié et
zlib Alpine1.4-r0 selon secdb officiel, sans upgrade global. Les scans et smokes
suivants déterminent l'effet réel. Les autres correspondances, dont des Critical,
restent **potentielles à qualifier** et bloquent la distribution. Le statut
« wont-fix » d'une distribution n'est pas un faux positif automatique.

Pour chaque avis : PURL/version/chemin public dans l'image, advisory, source/dates,
conditions d'attaque, parcours atteignable ou non démontré, correctif/backport,
preuve/test et décision humaine. États : applicable confirmée, potentielle,
faux positif documenté, version non atteignable démontrée, non testé. Aucune
suppression de correspondances par défaut. Les compteurs sont des couples
paquet/avis, pas un nombre d'exploits indépendants.

## Droits et secrets : checklist bloquante

- Licence racine Nova Atlas : absente à la racine lors du dossier RC ; décision
  du titulaire nécessaire, aucune licence choisie par l'agent.
- Code et dépendances redistribués : licences/notices exactes et obligations ;
  SBOM n'accorde aucun droit et n'assure pas une analyse juridique complète.
- Edge : licence du client distincte des conditions du service et des voix ;
  droit de radio publique/commerciale à confirmer par l'opérateur.
- Piper/Kokoro/autres : modèle/poids/version/voix/source et consentement, pas
  seulement licence du code. Aucun téléchargement/qualification implicite.
- Scripts éditoriaux : provenance, droits de reprise des sources et corrections.
- Musiques et habillages : titres/fichiers, titulaire, diffusion web/mobile,
  territoire, durée et usages privés/publics/commerciaux ; rien inventer.
- Images : preuve CC BY 4.0/attribution/changements/restrictions tiers ; révocation
  conservée. Licence inconnue => pas de publication automatique.
- Logo, icônes et ressources APK : origine/licence/notices à examiner.
- Ads/Premium : restent désactivés ; consentement, Data Safety et droits avant
  toute réactivation. Aucun achat ou accès compte publicité/paiement.
- OpenRouter historique : propriétaire doit confirmer révocation/rotation et revue
  d'usage avant réactivation ; l'agent ne prétend pas l'avoir révoquée. Aucun ancien
  secret affiché, transmis ou réutilisé. Aucun fichier privé machine lu par cet audit.

## Rollback et étapes suivantes

Arrêter les outils de collecte, puis les seuls services/projets autorisés. Conserver
rapports et état après incident, ne pas effacer un journal corrompu. Revenir aux
images par digest conservé après revue des risques : le rollback vers une version
vulnérable n'est pas une mesure de sécurité. Restaurer dans un chemin neuf et comparer
les écritures apparues depuis le snapshot avant de basculer. Retour frontend par
routage Flask, APK antérieur seulement avec signature/version compatibles.

Ordre opérateur : lever capacité disque/autorisation A ; installation neuve et
restauration A ; reproduire B ; trois téléphones avec HTTPS ; radio courte puis
24 h réservées et restart ; TTS réel/écoute autorisés ; triage CVE et droits ; revue
humaine des portes. Tant que ces éléments manquent : **NO-GO public**, essais privés
seulement après leur autorisation spécifique. Ne pas fusionner ces PR automatiquement.

Sources : [Syft](https://github.com/anchore/syft/releases/tag/v1.55.0),
[Grype](https://github.com/anchore/grype/releases/tag/v0.121.2),
[Guava](https://github.com/google/guava/security/advisories/GHSA-xxph-c9ww-hj94),
[Python3.12.16](https://www.python.org/downloads/release/python-31216/),
[secdb Alpine](https://secdb.alpinelinux.org/v3.24/main.json),
[Media3 TransferListener](https://developer.android.com/reference/androidx/media3/datasource/TransferListener).
