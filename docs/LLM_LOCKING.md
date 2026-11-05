# Ressources LLM exclusives — Sprint 01D

## Contrat et périmètre

`OllamaClient.call` et `ollama_call` gardent leurs paramètres et retours historiques
(texte, ou chaîne vide en cas d'échec). Ils acquièrent désormais un verrou OS dans
`modules/core/llm_lock.py` avant toute génération des backends existants `ollama`
et `llama-server`. Ces deux backends sont actuellement traités comme des ressources
à capacité un, même si llama-server est hébergé sur un autre PC de confiance.
Aucun fournisseur cloud, modèle, serveur ou service n'est activé par ce sprint.

La section exclusive couvre la génération, son éventuel retry HTTP et l'écriture du
cache. Un cache hit peut être servi sans verrou de génération. Après une attente,
le cache est revérifié sous verrou pour éviter de générer deux fois le même contenu.
Les méthodes privées de transport supposent cette admission ; les modules métier
doivent continuer à passer par call/ollama_call, jamais par les méthodes privées.

## Primitive et propriété

- `fcntl.flock(LOCK_EX | LOCK_NB)` sur un fichier régulier ouvert O_RDWR : acquisition
  atomique entre processus Linux. Chaque tentative utilise une ouverture distincte,
  donc les threads d'un même client se disputent aussi la ressource correctement.
- Fichier stable, **jamais supprimé, tronqué ou remplacé par le nouveau protocole**.
  Sa présence n'est pas un indicateur d'occupation. Le noyau décide de l'exclusivité.
- Bail privé par thread : descripteur, PID, identifiant de thread et jeton de diagnostic
  unique. Un client/thread non détenteur ne peut ni déverrouiller ni modifier les
  métadonnées du détenteur. Une acquisition récursive est refusée ; un échec ne
  déclenche jamais le finally de libération d'une autre demande.
- Échec fermé en cas d'ouverture, permissions, flock ou métadonnées impossibles.
  Les liens symboliques/durs et les fichiers d'un autre UID sont refusés. Il n'existe
  aucun fallback vers un simple fichier témoin ni vers une génération sans verrou.
- Le finally de call libère lors des retours et exceptions, y compris BaseException.
  Le context manager `LocalLLMLock.hold()` lève LockUnavailable si l'acquisition échoue :
  son corps ne s'exécute jamais sans admission. L'API booléenne acquire exige de
  vérifier le résultat et de libérer depuis le thread propriétaire.
- Les descripteurs ne sont pas héritables après exec. Les hooks fork ferment les
  copies dans l'enfant **sans LOCK_UN**, afin de ne pas déverrouiller le parent ni
  prolonger son bail après sa mort. L'enfant doit acquérir son propre bail ; il ne
  doit pas poursuivre une section critique héritée en supposant celle-ci valide.

Après mort du détenteur, y compris SIGKILL, le noyau libère le verrou lorsque ses
références de descripteur sont fermées. Aucun âge, TTL ou PID trouvé dans un JSON
n'autorise à voler un bail vivant. Le code vérifie l'inode lors de l'acquisition,
mais le répertoire reste une frontière de confiance : un opérateur ayant les droits
d'écriture ne doit jamais supprimer/remplacer le fichier pendant l'exploitation.
Un verrou advisory ne protège pas contre un programme qui ignore ce protocole.

## Configuration des ressources

Par défaut, tous les callers de l'installation partagent `data_dir/ollama.lock`.
Les paramètres optionnels suivants sont réservés à la configuration locale de
confiance, sans éditeur web supplémentaire :

```yaml
llm:
  lock_dir: data
  lock_resource: local-llm
```

`lock_dir` vaut data_dir si absent. `local-llm` conserve le nom historique ; les
identifiants personnalisés deviennent `llm-<sha256 tronqué>.lock`, sans interpoler
l'identifiant dans un chemin. Deux ressources réellement indépendantes peuvent
utiliser deux identifiants ; deux modèles qui partagent le même GPU/serveur exclusif
doivent garder le **même** identifiant. Aucun nom de modèle ou fournisseur n'est
utilisé automatiquement pour séparer les verrous.

**Même ressource = même répertoire partagé, même identifiant, même inode et même UID.**
Des checkouts, conteneurs ou data_dir différents doivent définir un lock_dir commun
(local ou volume bind commun) et des droits cohérents. Deux copies privées de ce
répertoire ne coordonnent rien. Ne pas changer le mapping de ressource tant que
l'ancien groupe de processus travaille encore.

Le périmètre validé est Linux et un système de fichiers POSIX local de confiance.
Windows sans fcntl refuse les générations locales ; aucun remplacement non sûr.
Les sémantiques NFS/SMB, verrous distribués et clients sur plusieurs hôtes ne sont
pas validés : leur gestion exigera un service de capacité approprié.

## Attente, priorités, annulation et arrêt

Le budget d'attente est monotone : `timeout + 60 s` dans call, ou wait_max explicite
pour acquire (défaut 1 200 s). `wait_max=0` signifie une seule tentative immédiate.
Les essais en contention attendent au plus 100 ms entre vérifications, sans boucle
active. Timeout, annulation ou erreur donnent un refus ; aucun appel fournisseur
n'est lancé. Un timeout de génération non positif ou non fini est refusé aussi.
Les opérations filesystem elles-mêmes dépendent du système local : ce n'est pas
une garantie d'annulation temps réel d'un syscall bloqué sur un stockage défectueux.

Les labels et anciennes valeurs CALLER_PRIORITY restent disponibles pour compatibilité,
mais **aucune priorité ne préempte**. Le vol après 30 secondes, l'expiration après
1 200 secondes et l'exception d'attente fetch/flash à 1 800 secondes sont retirés.
Tous les callers respectent le même protocole borné. Ce polling n'est ni FIFO ni
une file de priorités et ne garantit pas l'absence de famine sous contention continue.
Une demande finit néanmoins par acquérir ou abandonner son attente ; les échecs
restent observables. Une équité stricte/pondérée sera un travail distinct, sans
réintroduire l'interruption d'une requête active.

call/ollama_call acceptent en plus `cancel_event`. Une annulation avant/durant
l'attente refuse la génération ; elle ne tue pas une génération déjà commencée.
Les callers actuels n'y raccordent pas automatiquement l'Event du scheduler :
l'arrêt normal du Sprint 01C continue de drainer les appels et leurs attentes.
L'attente d'un bail et le timeout du fournisseur sont deux budgets séparés.

CLI conserve subprocess.run(timeout). HTTP conserve le timeout par tentative et
son retry cold-start : la durée fournisseur peut donc atteindre environ deux
fois timeout plus cinq secondes. Un timeout/une exception libère le bail du client.
Cela **ne prouve pas** que le serveur distant ou le daemon Ollama a effectivement
arrêté son calcul après déconnexion ; cette coordination doit venir du fournisseur.
Aucune expiration de fichier ne prétend résoudre un serveur bloqué. Les probes
/v1/models et la gestion existante du processus llama-server restent hors section
de génération ; leur gestion multi-processus n'est pas refondue ici.

## Métadonnées, cache et journalisation

Un fichier `*.lock.meta` distinct est remplacé atomiquement (temporaire 0600,
flush/fsync puis replace). Il contient uniquement version, état, PID/thread,
caller normalisé, jeton de diagnostic et timestamp. Ni prompt, texte généré,
URL, nom de modèle, clé API ou credential. Après un crash il peut annoncer
`held` alors que le noyau a déjà libéré : ce diagnostic n'est jamais une permission.
Un échec d'écriture initiale refuse l'acquisition ; un échec d'actualisation à la
libération ne doit pas conserver le verrou OS. Les temporaires sont nettoyés.

Le cache garde son format et TTL. Ses fichiers txt/meta étaient déjà remplacés
atomiquement, mais la paire n'est pas une transaction ; la documentation erronée
annonçant un verrou de cache est corrigée. Les écritures des générations d'une même
ressource sont sérialisées ; plusieurs ressources partageant volontairement une
même clé/cache, les anciennes entrées, la purge et le versionnement modèle/prompt
restent des limites distinctes. Les clés actuelles sont produites en interne, pas
acceptées comme chemins d'une API publique. Aucune migration des caches existants.

Les logs du verrou distinguent acquisition, attente, timeout, annulation, erreur
et libération refusée, sans URL ni valeurs arbitraires de caller. Les logs du client
ne reproduisent plus l'URL de backend, le corps/raison d'erreur HTTP ou le stderr
brut du modèle, susceptibles de contenir des credentials. Les fichiers de diagnostic
usuels sont ignorés par Git ; les credentials restent exclusivement hors dépôt.

## Déploiement et préparation LiteLLM

**Ne pas mélanger anciens et nouveaux processus.** L'ancien code ignore flock et
peut encore forcer/supprimer le fichier. Lors d'une intervention autorisée, drainer
et arrêter tous les processus de l'ancienne installation avant de déployer la
nouvelle version ensemble. Cette PR ne déploie rien et n'interrompt aucun service.
Un ancien fichier témoin laissé après arrêt peut être réutilisé comme inode ; son
contenu historique n'est pas une autorité. Ne pas lancer de nettoyage périodique
qui effacerait les fichiers .lock parce qu'ils semblent anciens.

LOCAL_EXCLUSIVE_PROVIDERS ne contient que les deux backends existants. Un provider
inconnu est refusé et n'est plus implicitement envoyé au CLI Ollama. Les futures
API cloud devront utiliser un adaptateur de concurrence bornée, quotas/rate limits
et budgets LiteLLM, **sans ajouter ces providers à la liste des ressources locales
exclusives**. Aucun verrou global unique pour toutes les API cloud n'est introduit.

Références : [flock Linux](https://man7.org/linux/man-pages/man2/flock.2.html),
[fcntl Python 3.12](https://docs.python.org/3.12/library/fcntl.html).

## Extension Sprint 03

Le backend litellm n'acquiert pas le flock global dans le client. Son relais de
sortie acquiert le même resource_path/UID/data pour chaque ressource locale,
coordonnant les appels directs historiques du même hôte. Le cloud a des slots
séparés par fournisseur. Le cache gateway a des verrous par requête. Aucun appel
direct externe sur un autre hôte n'est couvert ; voir LLM_GATEWAY.md.
