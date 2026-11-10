# Radio Studio — Sprint 11

## Périmètre et activation

Socle : dev après fusion PR18, `c3e5895` ; aucun déploiement réel. Studio **désactivé
par défaut**, Edge et les URLs Icecast/Android conservés. Sans option, le scheduler
historique reste actif avec sa politique en mémoire. Les améliorations de sécurité
du transport (backoff, nettoyage, conservation après échec) s'appliquent aussi au
mode historique et sont les changements intentionnels hors activation du Studio.

```yaml
radio_studio:
  enabled: false
tts_bridge:
  enabled: false
```

Voir `config/radio-studio.example.yaml`. Pour activer, l'opérateur prépare sa
configuration privée, sauvegarde puis redémarre proprement Radio et Web dans sa
fenêtre de maintenance. Aucune commande de démarrage n'est exécutée par ce sprint.
Le stockage data/audio_queue doit être le même stockage POSIX local et UID partagé
entre Web et Radio, comme dans DOCKER_DEPLOYMENT.md. Aucun PostgreSQL opérationnel.

## Audit et reproductions

| Parcours | Avant | Résultat livré |
|---|---|---|
| Admission :25/:55 | ThreadRunner et historique uniquement en mémoire | Identité civile réservée dans le journal ; redémarrage ne régénère pas un job connu |
| Génération | Articles → LLM → script → manifeste en mémoire | Références hash, dates et métriques privées ; aucun prompt/texte complet conservé |
| Publication | MP3/reçu atomiques, pas de lien durable au créneau | Nom réservé avant build ; MP3 récupérable si crash avant notification |
| File principale | Queue Python, admission récente en mémoire | Outbox du job avant queue ; issue ambiguë après crash explicite |
| Retour de lecture | MP3 supprimé même sans preuve de succès | Fichier conservé après échec ; en Studio, conservé même après succès local |
| Android | Copie atomique et dernier valide déjà existants | Protections conservées, observations et reprise du cache ; fallback vers autre valide si corruption |
| FFmpeg/Icecast | Relances immédiates, écritures bloquantes, nettoyage incomplet | Backoff 1–30 s, pipe non bloquant borné, terminate/kill/wait des processus possédés |
| Connexion | Log annonçait « connecté » après Popen | Seulement lancement FFmpeg et octets acceptés localement ; connexion/audition non vérifiées |

Les trois premières reproductions (`test_radio_studio_reproductions`) échouaient
avant correction. Certaines protections fonctionnaient déjà : refus sans reçu,
validation MP3, copie Android avant suppression principale, anti-doublon récent.
Elles ne sont pas présentées comme de nouveaux correctifs.

## Calendrier unique

`config/bulletins.yaml:post_hours` reste l'autorité. Préparation à :25/:55 pour la
cible :30/:00 ; les créneaux virtuels futurs sont affichés comme planned.
L'option Studio utilise explicitement Europe/Paris. Identité
`SHA256([type, Europe/Paris, YYYY-MM-DDTHH:MM])[:32]`, fold volontairement ignoré.
Une heure répétée ne génère pas deux identités. Une heure inexistante n'est pas
rattrapée ; lancement calculé sur la timeline réelle, p. ex. 01:55 → 03:00 au
passage printanier. La configuration est relue par le scheduler Radio existant.
**Aucun scheduler dans Flask.**

Les cibles futures ne sont pas toutes persistées comme jobs. Le journal réserve
un job lorsque le créneau devient admissible. Un créneau observé pendant une
génération active est skipped avec motif. Un créneau non observé pendant un arrêt
n'est pas inventé après coup. La configuration invalide conserve les dernières
heures du scheduler ; le dashboard signale un calendrier illisible plutôt que de
présenter un horaire certain. `first_slot_hour` ne crée aucune règle concurrente.

Dates distinctes : cible civile et lancement prévu, admission du job,
generation_started_at, generation_finished_at, admitted_at par canal,
local_play_started_at et local_play_finished_at. Les deux dernières sont des
observations du lecteur **local**, pas des dates certifiées d'arrivée à l'auditeur.
Les nouvelles observations utilisent UTC avec timezone ; le calendrier affiche Paris.

## Journal et outbox

`data/radio_studio/journal.json`, schéma 1, privé. Tables JSON : jobs (par ID),
channels (dernières observations), runtime_id, enabled, controls et demande de
réconciliation. Un job contient type bulletin/flash, créneau ou identité manuelle,
profil, références hash des articles, dates, tentatives, nom préalloué, publication
(nom/reçu/SHA256/taille/durée), métriques, événements et états des canaux.

| État | Signification |
|---|---|
| admitted | Créneau réservé, pas encore de succès métier |
| generating | Génération active ou interrompue sans mise à jour connue |
| ready | MP3 et reçu vérifiés ; pas encore de diffusion prouvée |
| queued | Admission principale enregistrée avant queue mémoire |
| playing | Début du lecteur local observé |
| completed | Passage principal local terminé, décodeur OK, octets acceptés par le pipe et source vivante ; pas une preuve d'écoute |
| skipped | Minimum d'articles non atteint ou génération précédente active |
| failed | Échec de génération connu |
| interrupted | Travail interrompu sans publication complète connue |
| recovery_required | Publication/admission/lecture ambiguë ou intégrité devenue invalide |

Les états de chaque canal restent séparés. Un bulletin peut être prêt sur Android
mais en échec sur le principal. Android répète ses passages ; completed du job
concerne le passage principal local, pas l'arrêt de sa boucle Android. Les flashs
restent ready après génération, disponibles en téléchargement privé, sans mise en
file automatique sur les deux canaux. L'ancien « done » de l'API flash signifie
toujours génération terminée, pas diffusion.

Un seul inventaire autoritaire : le job et ses canaux sont l'outbox. La Queue Python
est une projection de transport, pas une seconde source de vérité. La copie Android
est référencée par le même nom/empreinte ; aucune nouvelle base de médias.

Transactions : journal.lock stable, relecture et validation sous flock, version
SHA256 attendue pour les contrôles, temporaire 0600, flush/fsync/replace/fsync.
Fichiers spéciaux, hardlinks de journal et symlinks refusés. Journal corrompu ou
schéma inconnu : conserver l'original, refuser les mutations/générations ; ne pas
le remplacer par une liste vide. Une erreur de télémétrie ne doit pas arrêter la
musique. Le Web ne crée pas de journal au simple GET quand le Studio est absent.

Bornes : 1024 jobs, 64 événements/job, 128 actions, 4 MiB de journal, dix contrôles
par minute. Au plafond : refus explicite, aucune purge de publication ou suppression
silencieuse d'historique. Pas encore de CLI d'archivage : prévoir une maintenance
hors fonctionnement avant saturation, conserver la copie complète ; ne jamais
supprimer des lignes actives pour libérer de la place. Des limites de rétention
plus avancées seront une tranche complémentaire.

## Concurrence, notifications et récupération

`runtime.lock` garantit un propriétaire Radio Studio local ; `generation.lock`
coordonne bulletins et flashs Web, sans supprimer les fichiers de verrous. La mort
du détenteur libère le verrou OS. Les leases de service et le verrou LLM existants
restent applicables. Ce n'est pas une coordination distribuée multi-machines.

Nom préalloué : bulletin_DATE_HEURE_ID.mp3. Le builder conserve les règles strictes
de publication audio ; aucun MP3 incomplet n'est attaché au job. Crash après
publication mais avant attach : réconciliation vérifie le nom attendu, MP3/reçu et
empreinte, puis ready. Pas de scan qui abonne arbitrairement des fichiers anciens
à un job. Les MP3 historiques sont conservés ; sans reçu valide, jamais admis.

Au redémarrage, si une génération Web détient encore la lease, la réconciliation
est différée et son job reste generating. Sinon un nouveau runtime marque les générations sans publication
interrupted, retrouve les publications complètes et marque les admissions/lectures
anciennes recovery_required. Aucun rejeu automatique des jobs prêts ou ambigus.
La boucle Android conserve son principe historique explicite : dernier bulletin
valide en boucle, même si son ancien job n'est pas suivi par le Studio.

La reprise administrative porte sur **un MP3 existant**, avec version et ack de
relecture possible. Elle ne génère pas un nouveau bulletin, ne peut reprendre un
completed, et refuse une lecture/admission encore active du runtime courant.
Elle réinitialise seulement l'admission principale concernée ; Android déjà reçu
reste conservé. Un échec de notification laisse la publication récupérable.
Il n'y a pas de bouton HTTP de régénération LLM dans cette première tranche.

La réconciliation demandée est traitée au tick Radio suivant hors génération
active ; les observations du runtime courant ne sont pas réinitialisées. Si Radio
est arrêté, la demande reste en attente jusqu'au démarrage. Le verrou occupé
retourne 409 pour un contrôle incompatible, sans tuer le traitement.

**Aucune garantie exactement-une-fois de diffusion Icecast.** Une panne entre
écriture locale et observation durable peut laisser une issue inconnue. Une reprise
explicitement autorisée peut rejouer une partie déjà entendue. Une absence de rejeu
peut laisser un passage non entendu. L'intégrité des octets n'est pas une preuve
d'audience, et le Studio ne mesure aucun nombre d'auditeurs.

## Continuité et supervision

Principal : playlist historique, validation/décodage local des musiques sélectionnées,
cache de validation borné par nom/mtime/taille ; invalides/symlinks refusés. Dossier
vide ou aucun MP3 valide : silence MP3 de secours, attente interruptible. La
validation d'un fichier musical peut prendre jusqu'au timeout de décodage ; pas de
scan intensif illimité. Les fades existants restent simples, sans nouveau mastering.

Android : cache et reçus atomiques, nouveau courant seulement après validation.
Échec de copie conserve l'ancien. Courant corrompu/disparu : recherche d'un autre
bulletin valide, sinon silence. Le remplacement pending conserve le comportement
historique réel d'interruption de la passe, et non une fausse promesse de transition
sans coupure à la fin exacte du fichier.

Transport commun : MP3 locaux/file/pipe pour les étapes de traitement, pipe source
non bloquant avec timeout de cinq secondes, writes heartbeat/audio sérialisés,
backoff de redémarrage plafonné à 30 s. Les buffers de silence d’une seconde
sont cadencés à une seconde pour éviter de saturer le pipe au repos. Décodeur arrêté et attendu dans finally ;
source et heartbeat arrêtés à la sortie du worker. Le mode debug n'accumule plus
un stderr FFmpeg pouvant contenir l'URL Icecast. Les logs ne prétendent plus qu'un
Popen a établi la connexion.

Observations privées : lancement/epoch FFmpeg, échecs, nombre de démarrages et
reconnexions, date/volume d'octets écrits localement (échantillonnage toutes les
cinq secondes). Le dashboard affiche l'âge ; au-delà de quinze secondes,
observation non fraîche. `icecast_connection_verified` et
`audience_delivery_verified` restent null : aucune API Icecast ni ACK de réception
n'est prétendu. Un process vivant ou un pipe ouvert ne garantit pas le réseau.
Les healthchecks Compose restent des indicateurs distincts et limités.

Les délais d'arrêt du scheduler/Compose restent nécessaires : le Studio arrête les
admissions, les nouveaux segments observent l'annulation ; le LLM en cours et Edge
historique restent soumis à leurs timeouts existants. Le bridge Edge permet une
annulation async. Un décodeur musical en validation peut retarder l'arrêt. SIGKILL,
OS bloqué ou réseau hors service ne permettent pas de promettre la continuité.

## Bridge TTS facultatif

`modules/radio/tts_bridge.py` est un contrat runtime séparé. Il n'importe ni
modules/tts_lab, ni corpus, ni rapports/benchmarks. Les garanties et le vocabulaire
du laboratoire sont réutilisés conceptuellement, pas ses outils d'expérience.

Par défaut : chemin Edge existant, aucun bridge. En opt-in : profils bulletin et
flash, mappings explicites des balises `[VOIX1]` etc. vers engine/voice/language et
qualification opérateur. Les usages intro/breaking/transition/conclusion peuvent
être décrits dans la configuration mais n'ont pas de routage autonome tant que
le script ne porte pas une sémantique de section correspondante. Pas de promesse
que deux occurrences VOIX1 distinguent une introduction d'une conclusion.

Le runtime livré fournit **Edge français seulement**. Piper n'est pas qualifié et
n'est pas activable par un simple qualified=true : aucun provider Piper enregistré.
Une interface injectable permet les tests WAV/PCM simulés. Le bridge refuse les
artefacts marqués synthetic_fixture et les noms de publication finale, préserve le
texte entier, convertit WAV/MP3 vers un MP3 privé mono 24 kHz, et appelle le contrôle
MP3 complet sous budget. Il ne crée aucun reçu et ne publie jamais dans la queue.
Le pipeline 01F reste seul responsable du manifeste, concat, mix et reçu.

Fallback désactivé par défaut. Au plus une alternative explicitement qualifiée,
langue française compatible, voix déclarée, budget partagé avec le primaire ;
aucun téléchargement, clonage ou fournisseur payant. Chaque sélection/fallback
réussi est journalisé avec moteur/voix, sans texte. Les segments déjà validés sont
conservés par le pipeline ; un échec définitif bloque tout le bulletin. Pas de
substitution silencieuse ni revendication de qualification acoustique nouvelle.

`length_policy: warn` reste inchangé. Les 600–900 mots historiques restent
admissibles si structure, segments, durées, formats, concat, mix et MP3 sont valides.
Mots et durée réelle sont observés séparément ; pas de conversion certaine mots→minutes.

## API et interface privées

Page `/radio-studio`, lien depuis le Source Manager. Assets statiques, CSP conservée,
textContent, lecteur global persistant. Pas de scheduler ou polling réseau coûteux
au chargement ; actualisation explicite. Toutes les routes sont privées par défaut.

| Route | Contrat |
|---|---|
| GET /api/admin/radio | État, version, cent derniers jobs, calendrier deux jours, métriques, TTS non sondé |
| GET /api/admin/radio/jobs/ID | Détail d'un job connu, sans chemin absolu ni prompt |
| GET /api/admin/radio/jobs/ID/audio | MP3 connu et revérifié ; utile aux flashs après perte de l'ancien état Web mémoire |
| POST /api/admin/radio/control | JSON <=4 KiB, version SHA256, action allowlist ; session et CSRF |

Actions : dry-run (aucun fichier modifié ni génération), enable/disable des
**nouvelles admissions Studio** (ni kill, ni changement de YAML), reconcile,
verify (ID connu, résultat horodaté et journalisé), resume (ID + ack=true).
Conflit/version/lease/limite : 409. Aucune URL ou chemin audio arbitraire accepté.
Les contrôles nécessitent activation préalable par configuration privée ; pas de
contournement réseau/auth/CSRF ni bouton « tout redémarrer ».

Dashboard : observations des deux canaux, calendrier avec états des jobs connus,
durée réelle/événements/publication et vérification explicite, revue des échecs,
reprise du MP3 existant et politiques TTS. Moteur configuré n'implique pas modèle
chargé ou healthy. La lecture privée revérifie le reçu et le hash, sans décodage
réseau à la demande. Réponses no-store.

Métriques conservées lorsqu'observées : admissions, générations/publications,
échecs, moyenne de génération, temps TTS, segments/retries, durée audio, délais
fin de génération→admission et admission→lecteur local. Fenêtre limitée aux jobs
retenus ; aucune statistique de disponibilité 100 % ou d'auditeurs. Les événements
permettent de distinguer des dates manquantes de vrais temps nuls.

## Validation, exploitation et rollback

Tests : créneaux/DST, admissions multiprocessus, récupération flock après mort,
écritures interrompues, journal corrompu préservé, publication avant notification,
restart avant lecture, completion locale non rejouée, reçus absents/corrompus,
Android conservé, commandes strictes, TTS refus/timeout/annulation/fallback explicite,
texte long intact, fixtures interdites au bridge, auth/CSRF/CSP et navigateur.

Smoke : deux processus Python distincts sur volume temporaire, sinusoïdes et
FFmpeg réels, pipeline MP3/reçu, sortie avant attach, redémarrage, réconciliation,
admissions, récepteur fichier FFmpeg local, arrêt, cache Android et second restart
sans rejeu. Aucun Edge/LLM/Icecast réel. La CI l'exécute aussi dans Docker network=none.
Les fixtures synthétiques du smoke restent sur stockage temporaire de test, jamais
une publication de l'installation ; ce n'est pas un essai de radio Internet réelle.

Sauvegarde : arrêter les producteurs et streamers proprement, copier ensemble
journal, MP3/reçus et android_cache avec modes/UID. Conserver les verrous comme
fichiers stables ; ne pas supprimer ceux d'un système actif. Restaurer en stockage
neuf, vérifier les droits et les reçus, puis examiner recovery_required. Ne pas
annoncer une restauration d'écoute exactement-une-fois.

Dépannage : vérifier le calendrier YAML, l'âge des observations, les motifs de job,
le reçu et son empreinte via verify. Pour journal invalide, arrêter les producteurs,
conserver une copie et restaurer une version vérifiée après comparaison ; ne pas
écraser l'original en prétendant réparer. Pour Icecast, diagnostiquer réseau/service
séparément et garder les credentials hors logs. Une génération lente consomme le
créneau suivant sans chevauchement, pas un empilement de jobs illimités.

Rollback : arrêt coordonné, sauvegarde commune, remettre radio_studio.enabled et
tts_bridge.enabled à false puis redémarrage contrôlé ; conserver le journal et
les publications, ne rien migrer/détruire. Le mode historique ne consomme pas
l'outbox ; son historique en mémoire peut réadmettre une fenêtre encore ouverte.
Planifier le retour hors de cette fenêtre pour éviter une nouvelle génération.
Aucun déploiement ni service existant interrompu pendant ce sprint.

## Maintenance RC — Sprint 13

`python -m deploy.studio_archive` fournit une preview privée par cutoff/version.
Application uniquement après sauvegarde/restauration vérifiée et arrêt Radio ; leases
runtime/génération/journal. Archives immuables, index/tombstones durable avant retrait
terminal ; aucun MP3/reçu supprimé, aucun ancien créneau réadmis. Tous les fichiers
encore présents et derniers jobs par canal restent protégés. La maintenance peut
donc libérer moins que prévu : aucun contournement du quota 1024, pas de purge cachée.
Sauvegarder journal, archive_index.json et archives ensemble ; ancien runtime sans
lecture des tombstones incompatible avec une reprise sûre après compactage.

`deploy.radio_diagnostic` distingue observation de lancement, pipe local, mount et
octets client. Probe opérateur explicite, pas d'API arbitraire, enfant limité six
secondes ; octets seuls ne prouvent pas le décodage. Les courts smokes Icecast/FFmpeg
synthétiques ne sont pas une validation 24 h. Voir RELEASE_CANDIDATE.md.

## Qualification terrain

field_radio observe un mount et décode des échantillons privés bornés ; field_report
ne les assimile ni à une écoute ni à un uptime continu. Le protocole 24 h exige
une autorisation et des ressources réservées ; il n’est pas lancé automatiquement.
Voir FIELD_QUALIFICATION.md.
