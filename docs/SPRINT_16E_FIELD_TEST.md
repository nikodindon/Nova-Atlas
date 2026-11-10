# Sprint 16E — procédure de qualification opérateur

**Ce document n'a pas été exécuté sur atlas-lab.** Aucune autorisation de déployer,
redémarrer, générer ou benchmarker n'est déduite de la CI. L'installation active et
ses données restent intouchées par l'agent. Toutes les opérations ci-dessous sont
à choisir et exécuter par l'opérateur après les accords indiqués.

Installation concernée : `~/atlas-lab/Nova-Atlas`, état `~/atlas-lab/state`, projet
Compose `atlas-lab`, configuration **active** `state/config/config.yaml`. Les
exemples Git ne remplacent pas ces fichiers. Ne changer ni bulletins.yaml,
messages.yaml ni les modèles/arguments du serveur partagé pour qualifier 16E.

## 1. Lecture seule : versions, dépendances et disponibilité

Depuis le répertoire de déploiement, uniquement pour lire :

```bash
cd "$HOME/atlas-lab/Nova-Atlas"
export ATLAS_STATE="$HOME/atlas-lab/state"
git status --short
git branch --show-current
git rev-parse HEAD
gh pr view 22 --repo nikodindon/Nova-Atlas --json state,mergeCommit
gh pr view 23 --repo nikodindon/Nova-Atlas --json state,mergeCommit
gh pr view 24 --repo nikodindon/Nova-Atlas --json state,mergeCommit
# Remplacer NUMERO_16E par la PR livrée ; aucun gh pr merge.
gh pr view NUMERO_16E --repo nikodindon/Nova-Atlas --json state,headRefOid,statusCheckRollup
docker compose -p atlas-lab ps
curl --max-time 10 -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:15055/api/public/site
curl --max-time 10 -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:15173/api/public/site
```

Ne pas afficher compose.env, docker inspect Env, credentials ou configuration YAML
complète dans un rapport partagé. Un état healthy ou HTTP 200 ne prouve pas la
qualité éditoriale, la fraîcheur des données ni la réception audio par un auditeur.
La disponibilité du domaine public peut dépendre de Cloudflare ; distinguer son
refus et une panne locale.

Au départ de 16E : dev `0e8752622bbbe16cf9b3b187bb7d69a9b1e4533b`, PR #23/#24 ouvertes.
La PR #22 de qualification reste également ouverte au départ. L'installation peut
utiliser un checkout/des images issus d'une branche non encore intégrée à dev.
Avant toute construction destinée à la remplacer, inventorier ces différences et
conserver notamment les correctifs d'images/sécurité qu'elle porte. Une image
construite depuis cette seule branche basée sur dev n'est pas automatiquement
équivalente à l'image opérateur. L'ordre d'intégration/rebase et la conservation
de ces correctifs doivent être validés avant le déploiement, sans fusion automatique.

Vérifier de nouveau ces faits : ne jamais supposer une fusion. 16E fonctionne sans
elles ; les métriques/erreurs HTTP 16A et l'outil de comparaison 24 ne sont disponibles
que si effectivement intégrés. Si des conflits Git apparaissent après leur fusion
manuelle, les résoudre/tester dans un **worktree séparé**, jamais dans le checkout
qui sert les conteneurs. Conserver `NOVA_ATLAS_V2_PLAN.md` inchangé.

Dans le worktree 16E séparé, sans toucher à state :

```bash
python -m pytest -q tests/test_editorial_integrity.py tests/test_editorial_pipeline.py tests/test_editorial_trace_api.py
python -m deploy.editorial_evaluate --corpus tests/fixtures/editorial/review_corpus.json
python -m compileall -q modules deploy tests
git diff --check
```

Ces commandes utilisent des fixtures/doubles, pas le LLM partagé. Elles créent
seulement les caches/temporaires de tests dans l'environnement de développement.
Résultat attendu du corpus fictif : 3 anomalies signalées, 1 faux signal sur 8
cas valides, 0 faux blocage numérique strict. Ce n'est pas une mesure de production.

## 2. Préparation et sauvegarde — nécessite accord de maintenance

Créer un dossier de preuves privé hors Git et hors state, avec umask 077. Consigner
SHA source, identités d'images et configuration choisie **sans valeurs de secrets**.
Ne pas remplacer les images actuellement utilisées tant que l'accord de déploiement
n'a pas été donné. Garder les références d'images précédentes pour le rollback.

Une sauvegarde cohérente nécessite un arrêt coordonné des producteurs et mutations
Web, ou un snapshot cohérent déjà qualifié. **Ne pas déclarer les producteurs
arrêtés pendant qu'ils tournent.** Prévoir la fenêtre radio avec l'opérateur,
attendre les travaux actifs et baux LLM ; aucun kill, suppression de .lock ou arrêt
forcé pour accélérer une sauvegarde. Voir DOCKER_DEPLOYMENT.md/RELEASE_CANDIDATE.md.

Exemples, depuis un checkout contenant deploy.recovery :

```bash
# Lecture/inventaire seulement : dry-run, pas une sauvegarde cohérente validée.
python -m deploy.recovery snapshot --source "$ATLAS_STATE" \
  --destination /SAUVEGARDES/PRIVEES/atlas-avant-16e --include-secrets --exclude-pgdata

# ÉCRITURE, seulement après arrêt coordonné réellement effectué et autorisé.
python -m deploy.recovery snapshot --source "$ATLAS_STATE" \
  --destination /SAUVEGARDES/PRIVEES/atlas-avant-16e --include-secrets --exclude-pgdata \
  --apply --stopped
python -m deploy.recovery verify --source /SAUVEGARDES/PRIVEES/atlas-avant-16e
```

Le parent de destination doit exister et la destination doit être neuve. Cette
sauvegarde **contient des secrets** : stockage privé/chiffré, aucun upload ni artifact
public. Le dry-run ne crée pas la destination. Un refus pour contenu non pris en
charge n'autorise pas à contourner les garde-fous. PostgreSQL éventuel exige son
pg_dump distinct ; --exclude-pgdata n'est pas une sauvegarde SQL. Tester une
restauration dans un **nouveau** répertoire/projet avant de considérer la sauvegarde
restaurable. Ne jamais restaurer sur l'installation active ni faire down -v.

## Redémarrage News sans collecte supplémentaire

Pour une qualification limitée aux cycles RSS ordinaires, fusionner
`fetch.startup_fetch: false` dans la configuration active **uniquement lors de la
mise à jour autorisée**. Ce réglage supprime le fetch initial du processus News,
mais conserve les créneaux planifiés et les autres tâches. Sans cette option (ou
avec true), le comportement historique de collecte au démarrage reste inchangé.
Une collecte déjà en cours doit finir avant l'arrêt gracieux ; ce réglage ne
l'annule pas. Il ne change ni la radio, ni son calendrier, ni le modèle LLM.

## 3. Déploiement choisi et configuration exacte — écritures autorisées séparément

Après revue/fusion manuelle de 16E, construire/qualifier une image dans un checkout
séparé selon la procédure Docker existante. Ne pas faire git switch/reset dans le
checkout actif. N'effectuer la bascule d'images/redémarrage gracieux qu'avec l'accord
opérateur ; ce guide ne déclenche aucun déploiement.

Dans `~/atlas-lab/state/config/config.yaml`, **fusionner** le fragment sous le bloc
fetch existant, en préservant les autres paramètres. Ne pas copier tout l'exemple
Git à la place de la configuration opérationnelle. Première étape : conserver le
prompt historique avec le nettoyage sûr et collecter quelques preuves privées :

```yaml
fetch:
  editorial:
    prompt_version: legacy-v1
    checks: observe
    length_profile: auto
    title_policy: preserve_unknown
    source_languages: {}
    trace:
      enabled: true
      include_source: true
      include_raw_response: true
      retention_days: 7
      max_records: 20
      max_bytes: 2097152
      entry_max_bytes: 65536
```

L'activation des deux include_* est réservée à des sources dont la conservation
privée est autorisée. Sinon laisser false, avec la perte de capacité diagnostique
associée. Pas de sources intégrales ou rapports privés publiés. Valeurs par défaut
réelles : prompt legacy-v1, checks observe, trace désactivée et include_* false.
La configuration trace invalide est refusée à l'initialisation ; les pannes de
stockage/archivage pendant le traitement sont best effort et ne bloquent pas News.

Après la première revue, **changer seulement** prompt_version en `editorial-v2`
pour la phase suivante. Conserver checks observe. En v2 :

- Renseigner éventuellement source_languages avec des hostnames exacts vérifiés
  (par exemple un domaine dont tous les titres sont connus comme français → fr).
- Sans indice, preserve_unknown garde l'original. Si une traduction des titres
  inconnus est explicitement souhaitée, choisir translate_unknown ; le LLM est
  alors invité à conserver ceux déjà dans la langue cible. Pas de détection
  linguistique fiable ou correction grammaticale automatique revendiquée.
- Ne pas activer strict pendant ce premier essai. Il ne bloque ensuite que les
  signaux numériques, jamais tous les recouvrements lexicaux faibles.

Le rechargement de configuration existant peut réinitialiser le client LLM ; ne
pas annoncer un hot reload garanti de tous les processus. Employer le cycle de
maintenance existant validé, après drainage des travaux, sans relancer le modèle.
Ne pas modifier les budgets de #23 et le prompt en même temps pour une comparaison.

## 4. Quelques nouveaux articles, sans benchmark supplémentaire

Après autorisation du déploiement, observer **les cycles déjà programmés** et
sélectionner trois nouveaux articles traités après l'heure de bascule. Ne pas
lancer manuellement une collecte complète ou une édition/radio pour obtenir des
preuves. Les quotas RSS existants ne constituent pas un plafond global de trois
appels ; si ce plafond est nécessaire, utiliser un environnement isolé avec un
jeu de sources contrôlé, pas une prétendue limite de production.

Les articles publiés antérieurement et les reprises prepared ne sont pas des essais
du nouveau prompt ; ils ne sont pas régénérés. Un checkpoint ancien garde son
comportement de reprise. Enregistrer pour chaque nouveau cas :

- Hash article, catégorie, pub_date brute et timestamp de traitement distincts.
- Extrait transmis, titre original, version/hash du prompt, réponse avant nettoyage,
  résumé nettoyé, titre final et candidat d'article de la trace.
- Configuration du client/modèle, paramètres connus et inconnues explicites.
- État du journal, motifs et version/horodatage/empreintes des observations.

Les traces sont dans `state/data/editorial_traces/`. Lecture privée locale seulement.
Ne pas afficher les textes complets dans les logs ou une commande destinée à une PR.
Comparer le candidat de trace au JSON articles et au journal pour confirmer le
commit ; le statut en mémoire de la trace n'est pas un reçu transactionnel.

En présence du cas analogue fruits/dépistage : comparer **dans cet ordre** titre RSS,
extraction, entrée 3000 caractères, réponse brute, nettoyage et titre traduit.
L'absence d'entrée historique interdit de résoudre rétrospectivement la cause du
cas a5aed06a par simple ressemblance.

## 5. Refus, reprise, cache et logs

Attendus :

- `summary_source_insufficient`, `summary_invalid_contract`, etc. → retry existant,
  délais bornés ; aucune inscription seen ni texte de statut publié.
- Cache d'une réponse invalide ignoré, sans purge ; une réponse fraîche invalide
  n'est pas mise en cache. Pas de génération immédiate de correction ajoutée.
- Une source modifiée, langue/version/modèle/paramètre différent change de clé.
  Les anciens fichiers restent disponibles, mais ne deviennent pas une réponse
  du nouveau protocole. Les résultats invalides frais restent diagnostiquables.
- Un avertissement `Editorial observation` indique un signal, pas une fausseté.
  Le journal privé conserve les raisons ; une traduction valide peut être signalée.
- `Editorial trace unavailable` ne doit pas empêcher une publication normale.
  Vérifier permissions, quota, fichier inattendu/corrompu ou verrou occupé. Ne pas
  supprimer archive.lock ni corriger un JSON pendant le fonctionnement.

Les logs applicatifs existants peuvent être lus localement :

```bash
# Lecture seule sur l'instance ; ne pas coller les logs bruts dans GitHub.
docker compose -p atlas-lab logs --since 30m --tail=200 news
```

Ne pas mesurer la vitesse du modèle à partir de la seule durée de la trace : elle
inclut attente et cache. Tokens, finish_reason et cache_hit inconnus restent null.
Si #23 est intégrée, ses logs aident au diagnostic, mais un rapprochement temporel
approximatif ne constitue pas une attribution de métriques à un article.

Établir manuellement la part de signaux pertinents/faux positifs, avec références
source autorisées. N'activer checks strict qu'après acceptation des risques
numériques ; un montant distinct peut être légitime. L'absence de signal ne
constitue jamais une vérification factuelle.

## 6. Comparaison des prompts et modèles — autre machine/environnement réservé

**Aucune exécution sur le port 8080 partagé News/Radio.** Un autre nom de projet
Compose ne suffit pas à réserver CPU/GPU/RAM. Avant les commandes génératrices,
obtenir l'accord sur l'hôte réellement isolé, les ressources, les poids et la durée.
Vérifier versions exactes, quantification, empreinte des poids, template, contexte,
threads/offload et mode de raisonnement. Aucune commande de téléchargement implicite.
Ne pas charger un modèle alternatif dans le serveur de la radio.

Lorsque #24 est effectivement fusionnée et son schéma vérifié, utiliser son outil
**planificateur/analyseur hors ligne**. Il ne lance pas d'inférence. Préparer en privé
un corpus à partir des mêmes entrées autorisées, sans utiliser le résumé d'un modèle
comme vérité terrain. Pour la phase 1, rendre les deux prompts **avec le code 16E** :

```python
from modules.fetch.editorial import options, summary_prompt
# Ces valeurs viennent d'un cas autorisé dans l'environnement de qualification.
# input_text doit être le source_transmitted exact, pas une nouvelle extraction.
legacy = summary_prompt(original_title, input_text, category, target_language,
                        options({'fetch': {'editorial': {'prompt_version': 'legacy-v1'}}}))
candidate = summary_prompt(original_title, input_text, category, target_language,
                           options({'fetch': {'editorial': {'prompt_version': 'editorial-v2'}}}))
```

Créer deux cas distincts pour chaque même entrée, avec prompt_version différent,
reference_excerpt borné et référence annotée autorisée. Pour #24 : version 1,
cases avec id/task/prompt/prompt_version/reference_excerpt/reference_status.
Ne pas appeler un prompt synthétique d'exemple le prompt historique réellement rendu.

```bash
# Hors ligne ; écrit seulement un NOUVEAU rapport privé, pas l'installation.
python -m deploy.editorial_benchmark plan --corpus /PRIVE/corpus-prompts.json \
  --models /PRIVE/ornith-identite-complete.json --repetitions 3 --output /PRIVE/plan-phase1
# Après exécution explicitement autorisée sur le serveur de qualification dédié :
python -m deploy.editorial_benchmark compare --plan /PRIVE/plan-phase1/plan.json \
  --runs /PRIVE/captures-phase1.json --output /PRIVE/comparaison-phase1
```

La génération réelle doit appliquer les paramètres du plan, avec caches applicatifs
**désactivés** et une exécution séquentielle/bornée sur son serveur dédié. L'API
Chat Completions utilisée est celle du serveur de qualification, par exemple
`http://127.0.0.1:18080/v1/chat/completions` **sur l'hôte réservé uniquement**.
Ne jamais substituer silencieusement host.docker.internal:8080.

Capturer seulement le **texte final** et les compteurs allowlistés en mémoire vers
le format runs de #24. Ne pas enregistrer la réponse HTTP intégrale, qui peut
contenir du raisonnement. Chaque capture inclut plan_sha256, case/model/repetition,
prompt_sha256, parameters_sha256, output, durée/tokens disponibles, finish_reason et
erreur typée. Les délais s'appliquent réellement au transport exécutant ; le plan
seul ne les impose pas. Une déconnexion ne prouve pas l'arrêt du calcul distant.
Le transport d'exécution reste un outil opérateur isolé, pas une nouvelle commande
qui cible automatiquement une installation découverte.

Comparer après validation du contrat : enlever uniquement l'enveloppe JSON pour
évaluer le texte summary de v2, sans cacher les erreurs de contrat ; conserver le
texte exact avant/après nettoyage. Mesurer préambules, français, répétitions,
longueur, faits conservés/ajoutés, refus, coût et latence. Référence manquante →
fidélité non évaluable. Les durées fictives ne deviennent pas réelles.

Phase 2 : prompt choisi constant, mêmes entrées, Ornith/Qwen/Gemma seulement après
vérification des artefacts réellement disponibles. Un dérivé Uncensored ne vaut pas
le modèle officiel. Ne pas supposer 4 Go de VRAM suffisants à partir des paramètres
actifs d'un MoE. Renseigner dans le registre #24 révision, SHA, quantification,
runtime, matériel et reasoning_mode comparables. Laisser « non testé » si absent.
Aucun modèle gagnant sans sorties mesurées et revue humaine contradictoire.

## 7. Vérifier V1, V2 et Radio

Sur les mêmes trois articles, lire V1 locale 15055 et V2 locale 15173 : Unicode,
titre original/final, pub_date/timestamp, résumé, liens, absence de marqueurs JSON,
préservation du lecteur en navigation native. Vérifier /api/articles et
/api/public/articles : champs historiques préservés, aucune source brute ou trace.
Ne pas attribuer un fuseau certain aux dates historiques sans offset.

Observer la radio existante sans générer de bulletin supplémentaire : musique,
canal Android, absence de changement des reçus/MP3, ordres de segments et derniers
jobs. Le prompt radio, Edge, les horaires et les mécanismes de diffusion ne sont
pas modifiés par 16E. Une lecture navigateur réussie n'est pas une campagne
Icecast 24 h ni une mesure d'auditeurs.

## 8. Retour arrière et entretien privé — actions sur accord

- Désactiver trace.enabled pour arrêter les nouvelles captures ; aucune suppression
  automatique lors de la désactivation. La rétention se fait aux prochaines
  écritures : organiser une maintenance privée si un délai calendaire strict est requis.
- Revenir à prompt_version legacy-v1 et checks observe/off par configuration
  contrôlée. Les caches sont séparés ; aucun effacement requis.
- Si nécessaire, revenir aux images précédentes par la procédure de déploiement
  autorisée. Les nouveaux articles gardent les champs historiques ; ne pas restaurer
  une ancienne sauvegarde par-dessus les publications apparues depuis.
- Préserver les articles et prepared du journal. Leur reconstruction/modification
  historique nécessite une procédure et un accord distincts.
- Traces corrompues/temporaire abandonné : examiner/quarantainer seulement après
  arrêt coordonné des producteurs ; ne pas supprimer le verrou stable en activité.
- Aucun upload des traces, exemples ou sauvegardes vers GitHub/CI. Le partage avec
  ChatGPT reste une sélection privée autorisée, pas une publication du dépôt.

Consigner finalement : SHA/images, config non sensible, trois cas et raisons,
régressions éventuelles, faux positifs, données manquantes, décision de conserver
ou retirer le nouveau prompt. Ce document n'annonce aucune qualification terrain
réellement exécutée ni aucune amélioration qualitative déjà démontrée.
