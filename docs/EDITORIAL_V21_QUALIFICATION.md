# Qualification ciblée editorial-v2.1

## Statut et périmètre

**Non qualifié sur Ornith réel ; aucun nouveau déploiement autorisé.** Le correctif
est une proposition testable. Les tests CI et doubles HTTP valident des contrats,
jamais la qualité réelle du modèle. L'installation atlas-lab conserve son ancienne
image et sa configuration après rollback. Aucun exemple réel privé n'est commité.

## Audit de l'essai échoué

L'essai a observé six refus JSON structurés, avec arrêt normal et peu de tokens de
sortie. Le plafond de sortie n'a pas été atteint. La consigne « source non fiable »
visait la séparation données/instructions, mais pourrait être interprétée comme
un jugement sur la fiabilité journalistique. L'exemple JSON de refus en dernière
position et une condition large « insuffisante ou hors sujet » constituent deux
autres hypothèses. **Leur effet causal n'est pas mesuré.**

Les cinq autres échecs doivent être traités séparément :

| État observé | Nombre | Étape | Attribution LLM |
|---|---:|---|---|
| Extraction vide | 2 | Aucun texte exploitable retourné | Aucune |
| HTTP 403 | 2 | Téléchargement refusé | Aucune |
| HTTP 429 | 1 | Limitation du fournisseur | Aucune |

Aucun téléchargement supplémentaire, contournement de 403, changement de User-Agent,
retry agressif ou accès au serveur partagé n'a été effectué pour cet audit. Les
extraits historiques n'avaient pas été conservés. Leur contenu exact et la cause
des deux extractions vides restent inconnus. Il serait incorrect de déclarer ces
pages saines, réellement vides ou mal extraites à partir des seuls titres.

L'ancien rapprochement par heure peut attribuer la réponse de la tentative
précédente à un échec HTTP qui suit immédiatement. Les métriques individuelles
historiques restent **non attribuables avec certitude** sans identifiant ; les
agrégats observés ne deviennent pas des reçus de publication.

## Prompt et extraction

`legacy-v1` et `editorial-v2` conservent leurs prompts octet pour octet (empreintes
figées dans les tests). Le mode par défaut reste `legacy-v1`.

`editorial-v2.1` conserve le contrat JSON et le refus obligatoire si aucun fait
exploitable n'existe sur le sujet. Il demande de résumer un fait partiel pertinent,
même en une ou deux phrases, sans compléter les détails absents. Les accusations
restent attribuées. Le sujet est celui du titre ; une rubrique YAML inadaptée ne
constitue pas un motif de refus. La séparation de sécurité interdit de suivre les instructions
présentes dans SOURCE sans qualifier ses faits de non fiables. Un seul schéma de
réussite est décrit, puis le cas de refus est défini sans exemple JSON final à recopier.
Ces choix sont des hypothèses de correction, pas des résultats sur Ornith.

La sélection HTML historique peut ignorer une brève de moins de 200 caractères,
ou préférer un grand conteneur contenant du consentement à un corps plus précis.
Une reproduction synthétique place plus de 3 000 caractères de consentement devant
une courte information dans `itemprop=articleBody` : le parcours historique renvoie
le préambule, le nouveau sélecteur conserve le corps court.

L'extraction nouvelle est **réservée à editorial-v2.1** : corps explicites avant
conteneurs généraux, retrait de dialogues/éléments de consentement identifiés par
le DOM, conservation des paragraphes courts et Unicode. Aucun filtrage lexical du
mot « cookie », aucune correction de mots et aucune découverte réseau nouvelle.
Les bornes HTTP/SSRF, TLS, redirections et Retry-After restent celles du client
sécurisé. La sélection demeure heuristique : elle ne prouve pas la pertinence de
toutes les pages réelles.

Les trois prompts transmettent toujours les mêmes 3 000 premiers caractères du
texte fourni. Le plafond d'extraction antérieur reste en place. Les traces indiquent
la longueur du texte retourné, celle de la fenêtre transmise, leurs SHA-256 et le
plafond 3 000. Ces métadonnées fonctionnent sans conserver la source en clair.
Modifier cette fenêtre imposerait une variante/protocole distinct, pas une
comparaison présentée comme identique.

## Catégories et provenance RSS

L'audit privé rapproche `ingestion.source_key` (SHA-256 de l'URL de flux exacte) de
la configuration utilisée pendant l'essai. Les cas thématiquement surprenants
signalés correspondent à des flux généralistes rangés sous une catégorie
thématique. La collecte hérite de cette rubrique YAML ; elle ne reclasse pas le
sujet avec un LLM. Aucun défaut d'association entre réponse et article n'est
établi par ces cas. Les catégories déclarées dans RSS ne remplacent pas le choix
opérationnel YAML. Une URL déjà connue conserve sa catégorie historique.

Proposition limitée, **non appliquée** : examiner dans Source Manager le déplacement
des flux généralistes concernés vers `monde`, en conservant leurs URL exactes et
la vérification de version de configuration. Sinon, choisir des flux véritablement
thématiques après un test explicite autorisé. Aucun nouvel endpoint de flux n'est
inventé ou téléchargé ici. Aucun article historique n'est recatégorisé, ni le YAML
actif ou celui des exemples modifié automatiquement.

## Corrélation explicite

Chaque appel logique reçoit un UUID hexadécimal de 32 caractères. Le même
`request_id` figure dans le header LLM `X-Request-ID`, les logs de réponse et le
`generation_receipt` transmis directement à l'appelant. Les reçus sont locaux au
thread et à l'appel, sans variable globale contenant la dernière réponse.

- Une erreur avant `_summarize` n'a ni identifiant de génération ni métriques LLM.
- Un cache hit a un identifiant logique, `cache_hit=true`, `transport_attempted=false`
  et aucun nouveau nombre de tokens fournisseur.
- Une réponse HTTP a son propre statut, finish_reason, tokens, indicateur de
  raisonnement et durée de réponse. Le contenu du raisonnement n'est jamais stocké.
- La durée application inclut le verrou/cache ; `lock_wait_ms` et
  `response_duration_ms` permettent de distinguer les temps.
- CLI et passerelle conservent leurs contrats, mais les métriques non instrumentées
  restent inconnues. Un ID applicatif n'est pas une attestation du fournisseur.

Les traces privées existantes restent optionnelles et bornées. Les contrats JSON
publics et Android ne changent pas. Le champ `title_original` reste disponible pour
les deux modes structurés ; les clés de cache incluent leur version propre.

## Corpus et mesures

`config/editorial_qualification/editorial-v21-corpus.json` contient 13 textes
originaux synthétiques, placés sous CC0 : brèves françaises, information incomplète,
déclaration non vérifiée, anglais, japonais, arabe, russe, texte long, fait unique,
navigation, texte vide, sujet distinct et préambule dépassant la fenêtre. Le cas
du fait unique utilise volontairement une rubrique thématique inadaptée.

Les annotations concernent **la fenêtre réellement transmise**. Une information
située après 3 000 caractères ne peut pas être attendue du modèle. Le cas de
préambule tronqué mesure cette limite séparément de la correction d'extraction.

Le plan contient 39 appels (13 × 3 versions) par répétition. L'ordre des versions
est tournant et déterministe ; une à trois répétitions sont possibles. Les requêtes
utilisent les paramètres applicatifs actuels, température 0,3 et sortie maximum
4 096 tokens. **Le seed n'est pas envoyé** : le protocole est reproductible, pas
les sorties bit à bit. Le cache applicatif est désactivé pour cet essai ; le cache
KV côté serveur doit être déclaré dans le relevé opérateur s'il influence les temps.

Mesures automatiques : état de contrat, refus selon les annotations du corpus,
JSON syntaxiquement invalide (distinct des erreurs HTTP/absence de réponse),
nombre de mots, durées, tokens et finish_reason disponibles. Les refus en texte
libre de legacy-v1 exigent une classification humaine : leur nombre n'est pas
inventé à partir d'une absence de JSON.

La qualité du français et les faits inventés restent `null` jusqu'à une revue
humaine. La grille demande une note de français 0–4 avec citation de la sortie,
faits conservés/manquants et chaque affirmation non soutenue avec passage source.
Un score global LLM n'est pas une preuve de fidélité. Ce petit corpus n'établit
aucune qualité générale sur l'actualité réelle.

## Procédure simplifiée — opérateur et agent

L'opérateur décide des ressources ; **l'agent prend en charge les commandes et
les fichiers**. Deux décisions restent nécessaires avant les mesures : instance
Ornith dédiée ou fenêtre/réservation explicite sur des ressources isolées, puis
confirmation de son environnement exact. Changer simplement de port sur la même
machine/GPU ne démontre pas l'absence de contention avec Radio.

Préparer le plan, sans réseau ni modèle :

```sh
python -m deploy.editorial_prompt_trial prepare --output /DOSSIER_PRIVE/plan-v21
```

Après accord distinct seulement, l'agent vérifie sur l'instance dédiée : poids
Ornith 1.5 35B IQ2_M et SHA-256, version llama-server, matériel/driver, mode de
raisonnement, contexte, threads et offload réellement utilisés. Aucun téléchargement
ou démarrage de modèle n'est exécuté par l'outil. L'instance doit être réservée à
cet essai, éventuellement par tunnel local autorisé, sur `127.0.0.1:18081`.

Le fichier privé d'autorisation, fondé sur
`config/editorial_qualification/isolated-ornith-authorization.example.json`, reste
invalide tant que les valeurs nulles et les deux accords false ne sont pas remplacés
après décision réelle de l'opérateur. Il ne contient aucune clé fournisseur.
L'endpoint partagé 8080 est refusé ; les redirections et proxies d'environnement
sont désactivés. La destination et le modèle disponible sont vérifiés avant génération.
L'attestation d'isolation/matériel/poids appartient à l'opérateur : l'API model ID
ne certifie pas à distance le fichier GGUF ni le GPU.

```sh
python -m deploy.editorial_prompt_trial measure \
  --plan /DOSSIER_PRIVE/plan-v21/plan.json \
  --authorization /DOSSIER_PRIVE/autorisation-ornith.json \
  --output /DOSSIER_PRIVE/mesures-v21 --execute
```

Sorties privées, répertoire neuf hors Git et hors état actif : measurements.json,
review.md. Les deux sorties contiennent les fenêtres sources autorisées, titres,
catégories et annotations pour une revue autonome, sans relire le dépôt. Matrice complète exigée, cache désactivé, aucun retry de génération,
budget global d'admission et timeouts par appel. Une limite atteinte donne un
rapport incomplet, jamais un succès rempli artificiellement. Un timeout client
n'atteste pas l'arrêt instantané du calcul côté serveur ; l'opérateur supervise
son instance dédiée. Ne pas lancer plusieurs essais concurrents sur le même
serveur. Les verrous locaux de ce laboratoire ne coordonnent pas plusieurs machines.

## Porte avant un nouveau déploiement

| Preuve | État livré |
|---|---|
| Reproductions d'extraction et attribution | Tests hors ligne |
| Conservation des prompts legacy/v2 | Empreintes testées |
| Sources contrôlées et matrice 3 prompts | Plan générable, 13 cas |
| Sorties réelles Ornith IQ2_M | **NON MESURÉ** |
| Refus injustifiés/légitimes réels | **NON MESURÉ** |
| Qualité du français et hallucinations | **NON ÉVALUÉ** |
| Accord pour une nouvelle activation atlas-lab | **ABSENT** |

Avant toute nouvelle activation, exécuter et relire les sorties sur les mêmes
13 entrées avec les trois prompts. Le nouveau prompt doit produire des résumés
fidèles des neuf entrées exploitables et refuser les quatre fenêtres sans faits
pertinents ; expliquer toute divergence. Exiger zéro JSON invalide sur les cas
évalués, absence d'information inventée après revue et citations justifiant chaque
jugement. Ajuster ensuite le corpus si des annotations s'avèrent contestables.
Les tests avec HTTP simulé n'ouvrent aucune de ces portes empiriques.

## Compatibilité et retour arrière

Aucun réglage actif n'est modifié par cette PR. La sélection proposée, réservée à
une future autorisation, serait `fetch.editorial.prompt_version: editorial-v2.1`.
Les anciens prompts et caches restent disponibles. Le rollback News actuel reste
inchangé. Il n'existe ni migration, ni purge, ni modification d'Ornith, Radio,
V1/V2, des horaires, des secrets ou des données persistantes.

## Mode partagé explicitement autorisé (PC principal)

La commande `measure-shared` est distincte de `measure`. Elle accepte uniquement
le serveur local 8080 avec un fichier déclarant **mode=shared**, accord explicite
et **isolated_from_atlas_lab=false**. Elle ne fait pas passer un serveur partagé
pour une instance isolée. L'ancien mode dédié et son refus du port 8080 restent
inchangés.

Le préflight relève le modèle chargé et sa quantification, l'empreinte du GGUF,
le PID/build du serveur, ses paramètres, le matériel et les ressources disponibles.
Les paramètres serveur ne sont jamais modifiés. Les trois prompts utilisent les
mêmes paramètres de requête (température 0,3, plafond 4096, seed non envoyé). Le
cache de **réponses** est désactivé ; le cache KV existant n'est pas purgé et ses
métriques disponibles sont conservées.

Conditions contrôlées avant chaque appel et surveillées pendant celui-ci :
News arrêté, services existants sains, calendrier radio inchangé et fenêtre libre,
absence de travail Radio admis/en génération, nombre de threads du worker Radio
au repos, modèle/processus inchangés et mémoire disponible suffisante. Les slots
serveur doivent être libres avant admission. Une autre requête concurrente ou un
besoin Radio/News interrompt notre socket, sans arrêter ces services. L'annulation
côté fournisseur reste une propriété du serveur ; les slots sont recontrôlés avant
un nouvel appel.

Le verrou utilisé est exactement le `ollama.lock` existant, ouvert en lecture seule
et pris en `flock` non bloquant par appel. Aucun `.lock` ni `.meta` de production
n'est créé, remplacé ou modifié. Le verrou est libéré avant une pause de courtoisie
de deux secondes. Il n'est jamais détenu pour les 39 appels en bloc. Les fenêtres
radio sont conservatrices, incluent les deux folds DST et réservent une marge
avant l'admission anticipée et après le créneau.

Chaque tentative est inscrite atomiquement dans un checkpoint privé avant l'envoi.
Un résultat terminé n'est jamais relancé par `--resume`. Une tentative interrompue
avec issue inconnue reste marquée inconnue et n'est pas rejouée silencieusement.
Les reports et refus d'admission ne déclenchent aucune génération. Les timeouts,
refus et JSON invalides sont conservés sans retry automatique. Le texte final
exact (y compris espaces) est conservé lorsqu'il est disponible et sans raisonnement
interne ; les sorties tronquées restent signalées par leur finish_reason.

```sh
python -m deploy.editorial_prompt_trial measure-shared \
  --plan /DOSSIER_PRIVE/plan/plan.json \
  --authorization /DOSSIER_PRIVE/autorisation-partagee.json \
  --output /DOSSIER_PRIVE/run --execute
# Même commande + --resume après interruption, sans refaire les appels terminés.
```

L'agent prépare le fichier d'autorisation après l'accord opérateur ; les identifiants
et paramètres réels restent privés. Un fichier STOP dans le dossier de sortie
arrête l'admission et interrompt notre requête surveillée. Le budget global reste
compté depuis le premier démarrage, y compris après reprise. Aucun endpoint de
contrôle de conteneur, modèle ou fichier applicatif n'est modifié par cette commande.

## Révision ciblée V2.2 — préparation uniquement

`editorial-v2.2` est une nouvelle identité de prompt, opt-in. Les trois anciennes
variantes et les octets des prompts restent inchangés. Un jeu d'empreintes couvre
les 13 sources historiques avec les quatre profils de longueur (156 combinaisons).
Les caches restent distincts grâce à la version et au prompt effectif ; aucun
ancien cache ni article n'est modifié. L'extraction V2.2 conserve le parcours V2.1.

Le prompt demande une relecture dans **la même génération**, sans exposer le
raisonnement : accords/conjugaison, rôles ville/institution, conservation des
unités, attribution et absence d'ajouts tels qu'unanimité ou succès. Les exemples
observés inspirent ces consignes ; elles ne prouvent pas qu'Ornith les suivra.
Aucune réparation grammaticale regex ni appel correcteur supplémentaire.

### Contrôle explicable de discordance

La nouvelle règle `municipal_election_vs_standalone_match_v1` exige un titre
annonçant explicitement une élection municipale (français/anglais), puis un bref
corps (600 caractères maximum) dont la première phrase décrit un sport nommé,
une victoire et un score. Toutes les phrases doivent rester dans un contexte
sportif identifiable. Une mention du scrutin, d'un candidat, d'un vote ou du
maire, une citation, une négation ou un récit long écarte ce signal bloquant.
Les indices précis sont conservés dans le journal privé `editorial_review.evidence`.

- `checks: observe` : signal `needs_review`, publication conservée.
- `checks: strict` : ce signal bloque publication et cache, via le journal de
  tentatives existant (`editorial_needs_review`), sans marquer l'URL comme publiée.
- `checks: off` : aucun contrôle. Le comportement de cette option reste inchangé.

Une faible intersection de mots reste **seulement une observation**, même en mode
strict. Ni cette règle ni les contrôles numériques existants ne prouvent la vérité.
La règle est délibérément étroite : elle ne reconnaît pas toute contradiction,
les métaphores, ni toutes les traductions/langues. Les mentions d'un contexte
électoral peuvent également empêcher de signaler un mauvais article. Le nombre
de tests négatifs réussis n'est pas une mesure du taux de faux positifs réel.
L'opérateur garde `observe` pour la qualification initiale. Aucune API publique
n'expose le détail privé des indices.

### Petit essai proposé, sans exécution autorisée

Préparation hors ligne, sans accès au serveur ni à l'état de l'installation :

```bash
python -m deploy.editorial_prompt_trial prepare \
  --suite v22-focused --output /tmp/atlas-v22-focused-plan
```

Le corpus CC0 séparé `editorial-v22-corpus.json` contient **8 sources, 16 appels** :
V2.1 et V2.2 sur les mêmes fenêtres (3 000 caractères maximum), une répétition,
ordre alterné. Il couvre brève exploitable, déclaration Alizé, prix traduits de
l'anglais, musée/ville en russe, vote du jardin, navigation seule, élection/hockey,
et article mixte avec un véritable fait électoral. Les 39 anciens résultats ne
sont pas recalculés ; le corpus et la matrice historiques restent inchangés.

**Budget à faire valider avant toute inférence :** 16 appels réels au maximum,
un seul en cours, aucun pilote supplémentaire, aucun retry automatique,
`temperature: 0.3`, `max_tokens: 4096`, seed non envoyée, cache de réponses désactivé.
Timeout de 240 secondes par appel : plafond de 64 minutes d'appels, plus les
contrôles et deux secondes de pause entre appels ; proposer un budget global de
75 minutes. Ce plafond n'est pas une estimation de durée réelle. La charge reste
celle d'un serveur partagé, jamais déclarée isolée artificiellement.

Après accord distinct, refaire le préflight du mode partagé : News arrêté,
services préservés, identité/empreinte/paramètres Ornith inchangés, slots libres,
verrou disponible, mémoire suffisante, calendrier et jobs Radio relus. Il faut
une fenêtre restant compatible avec le budget et la marge Radio de 15 minutes,
sinon reporter l'essai. Ne pas réutiliser une ancienne autorisation signée par
son empreinte pour une nouvelle matrice. Les protections du mode partagé restent
identiques et la reprise ne répète jamais un appel déjà commencé.

Comparer refus légitimes/injustifiés, JSON, faits ajoutés/perdus, qualité du
français et rôles des entités avec citations, durées/tokens par request_id. Lire
les sorties exactes avant nettoyage. Comparer séparément le comportement du prompt
et le contrôle déterministe : un bon résumé du corps peut encore être impropre
à la publication sous un titre sans rapport. Aucune note de vérité automatique.
Une revue humaine/externe est requise avant de décider d'un déploiement.

### État et rollback

Aucun nouvel appel réel n'est effectué pour cette révision. Aucune amélioration
mesurée de V2.2 n'est revendiquée. PR #27 reste ouverte, sans fusion ni déploiement.
Le prochain essai exige News arrêté et un préflight à jour ; ne jamais déduire
son état courant de cette procédure. Modèle, horaires et configuration ne sont
pas modifiés par cette révision. Après une éventuelle installation autorisée, sélectionner une ancienne
`prompt_version` restaure son prompt et son namespace de cache ; `checks: observe`
restaure le traitement non bloquant. Ne supprimer aucun cache pour revenir en arrière.
