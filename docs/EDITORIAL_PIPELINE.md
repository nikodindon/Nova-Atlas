# Pipeline éditorial — Sprint 16E

Base : dev `0e8752622bbbe16cf9b3b187bb7d69a9b1e4533b`. Au début du lot, les PR #23
(16A) et #24 (qualification) sont ouvertes, non intégrées ; 16B/C/D ne figurent pas
parmi les PR ouvertes. Cette PR est autonome : aucune copie/cherry-pick de leurs
branches, aucune fusion ou intervention sur l'installation active. Les rapports
réels restent privés. Les fixtures de cette PR sont synthétiques.

## Audit et reproductions

Douze tests échouaient avant correction, avec un contrôle valide déjà réussi :

- Des mots valides sont supprimés : `de demande` → `demande`, `de demain` →
  `demain`. La regex prend une préposition pour un fragment du mot suivant.
- `202 2026` devient `2026`, même si les nombres désignent deux valeurs distinctes.
- Les caractères CJK disparaissent ; le `C` de `Plan C` est supprimé.
- Une parenthèse non fermée provoque la suppression de tout le reste du texte.
- `très très` est dédupliqué sans connaissance du sens.
- Un préambule conversationnel explicite reste présent.
- Le cache ignore langue, catégorie, modèle et les changements du texte après les
  500 premiers caractères.

Une autre reproduction, ajoutée avec le nouveau contrat, montre qu'une réponse
non vide mais invalide pouvait rester dans le cache et condamner les tentatives
suivantes à rejouer la même erreur. Les nouveaux tests vérifient sa non-mémorisation.
La passerelle ignorait aussi la **valeur** de cache_key : elle servait seulement de
booléen. Elle participe désormais à son espace de cache.

Ces démonstrations prouvent des défauts du code. Elles ne prouvent pas que toutes
les anomalies historiques, notamment les mots déformés ou amputés,
sont produites par ce nettoyage. Sans entrée et réponse brute attribuables, la
cause de chaque ancien exemple reste inconnue. Aucune réécriture historique.

## Étapes séparées

1. Le client SSRF existant télécharge la page ; extraction existante limitée à
   3500 caractères. Aucun nouveau téléchargement ou changement du parser.
2. Le résumé utilise **exactement content[:3000]** comme auparavant ; la trace
   peut conserver séparément extraction et entrée réellement transmise.
3. La réponse textuelle finale du client est conservable avant nettoyage.
   Ce n'est pas l'enveloppe HTTP brute ni le raisonnement du modèle.
4. `clean_editorial` retire contrôles/ANSI et préambules conversationnels explicites
   ancrés en début de texte. Il ne corrige ni orthographe ni fragments incertains,
   ne fusionne plus de mots/nombres et ne supprime aucune écriture Unicode.
5. Le texte public n'est plus passé dans `clean_for_tts`. Le nettoyage Markdown/TTS
   existant reste dans les consommateurs audio, sans modification de la radio.
6. Le titre, les observations et le candidat final sont enregistrables dans une
   trace privée. Le checkpoint et la publication JSON gardent leurs garanties 01E.

Le nettoyage ne retire pas un simple « Voici les résultats… » ou une note
substantielle par devinette. `editorial-v2` refuse les ouvertures conversationnelles
résiduelles dans son contrat plutôt que de supprimer une information ambiguë.
Les fragments historiques incorrects sont conservés pour diagnostic, pas remplacés
par une correction plausible présentée comme certaine.

## Prompts versionnés et compatibilité

Configuration optionnelle sous `fetch.editorial`, voir le fragment d'exemple.

| Dimension | legacy-v1 (défaut) | editorial-v2 (opt-in) |
|---|---|---|
| Instructions de résumé | Prompt historique « clair et complet », rendu conservé | Début direct, langue cible, faits/accusations/attributions, pas de contexte ajouté ni citation inventée |
| Longueur | Pas de cible supplémentaire | FR : brève 50–90, standard 90–160, complexe jusqu'à 220 mots ; cibles souples |
| Source courte | Compatible | Pas de minimum artificiel de mots ; source vide/non alphanumérique refusée, insuffisance sémantique signalable par le modèle |
| Sortie | Texte historique | JSON strict `status` + `summary`, sans champ supplémentaire |
| Refus source | Échec existant | `{"status":"insufficient_source","summary":""}` → échec typé, jamais texte publié |
| Titre | Prompt et garde historique conservés | Traduction fidèle sans reformulation ; titres de langue connue égale à la cible conservés sans appel |

Profil `auto` : brève si entrée <650 caractères, standard <2200, sinon complexe.
Ce classement par taille n'est pas une mesure de complexité sémantique ; réglages
brief/standard/complex explicites possibles. Pour les autres langues, pas de
contrainte française de décompte de mots ; consigne de concision proportionnée.
Aucun blocage automatique d'un résumé pour la seule longueur.

Le rendu des données v2 est JSON pour distinguer instructions et source ; ce n'est
pas une garantie d'immunité aux injections sémantiques dans un document.
JSON malformé, champs additionnels, statuts non reconnus, marqueurs internes et
sorties avec marqueurs de raisonnement connus sont refusés. Pas de seconde passe
LLM correctrice ou régénération immédiate ajoutée. Le journal de reprise gère les
tentatives bornées et délais existants. Une source changée donne une nouvelle clé.
Un contenu manifestement vide ou une sortie sans caractère alphanumérique ne
peut pas devenir une publication valide.

## Titres et langues

En v2, `title_policy: preserve_unknown` est conservateur : sans indice de langue,
le titre original est gardé. `source_languages` permet des indices explicites par
hostname ; ce sont des déclarations opérateur, pas une détection automatique.
Un domaine multilingue ne doit pas recevoir un indice uniforme non justifié.
`translate_unknown` autorise un appel sur langue inconnue ; le prompt demande le
retour à l'identique si le titre est déjà dans la langue cible. `preserve_all`
désactive les traductions. Une demande force ne contourne pas une langue connue
égale à la cible ou preserve_all.

Le chemin v2 n'utilise **pas** les listes de mots étrangers pour décider de la
langue : elles pourraient rejeter des noms propres. Le garde historique reste
uniquement dans le mode compatible. Aucun détecteur grammatical ou linguistique
fiable ajouté : une phrase latine incorrecte peut rester indétectée.

Les préambules explicites de traduction sont retirés ; sortie vide, multi-ligne,
ponctuation seule, raisonnement, marqueur d'erreur ou nouveau nombre → fallback
sur l'original avec motif. Une localisation légitime d'un nombre peut également
être refusée : ce garde est conservateur, pas un jugement de fausseté. Le titre
source est toujours rempli dans title_original en v2, même sans traduction.
Aucun changement du nom des champs publics.

## Observations déterministes, pas vérification des faits

`checks: observe` par défaut : raisons privées dans article_processing.json,
champ additionnel editorial_review, sans nouveau statut principal du journal.
Le signal porte sur les empreintes entrée/sortie, numéro de tentative et date
observée. `off` l'indique explicitement, sans recycler un ancien verdict.

- Faible recouvrement des termes significatifs entre titre et contenu/résumé, ou entre titre traduit et résumé :
  signal de décalage possible, jamais identité d'événement ni preuve de fausseté.
- Valeurs numériques uniques divergentes pour la même unité (euros/dollars/bus/
  semaines/jours/%) : signal explicable. Les unités à plusieurs valeurs ne sont
  pas interprétées automatiquement. Les espaces de milliers simples sont gérés.
- Noms propres, négations, causalité, équivalence sémantique et temps d'événement
  ne sont pas résolus par ces heuristiques. Aucun score de vérité.

`strict` ne bloque que numeric_disagreement, après qualification opérateur.
Les faibles recouvrements lexicaux restent consultatifs, même en strict : ils
peuvent représenter une traduction ou une paraphrase correcte. Un refus strict
utilise editorial_needs_review et les retries existants ; aucune entrée seen
n'est créée pour ce refus. Après épuisement, état failed avec motifs conservés ;
aucune suppression d'article ou relance illimitée.

### Corpus de contrôle reproductible

```bash
python -m deploy.editorial_evaluate --corpus tests/fixtures/editorial/review_corpus.json
```

11 cas **fictifs annotés** : 3 discordances détectées, 0 manquée, **1 faux signal
sur 8 cas valides** (traduction anglais → français, recouvrement lexical faible).
Précision des signaux : 3/4 ; rappel 3/3 ; faux positifs 1/8 ; faux blocages
numériques stricts 0 sur ce corpus seulement. Calcul observé environ 0,002 s lors
d'un essai local ; ne pas extrapoler aux performances LLM ni à un corpus réel.
L'absence de faux blocage ici ne garantit pas sa disparition en exploitation.
Un changement réel de périmètre/calendrier peut produire deux montants légitimes
mais différents : c'est une raison de garder observe pendant la qualification.

Le cas historique a5aed06a n'a pas d'entrée source conservée dans le dossier : sa
cause historique n'est pas résolue. Une fixture analogue fruits/dépistage est
signalée et les futures traces permettront de départager extraction, génération,
traduction et nettoyage. Aucun article médical réel publié dans cette PR.

## Cache : identité et admission

La clé SHA-256 inclut tâche, version de prompt, langue, catégorie, prompt complet
(donc tous les caractères réellement transmis), identité/configuration du modèle,
révision/empreinte déclarée si renseignée, paramètres disponibles et politique
passerelle. L'endpoint participe à l'identité sous empreinte, pas aux logs/traces.
Le snapshot du client est lié à l'appel pour qu'un remplacement du client global
ne génère pas avec B sous l'identité de A ; un test reproduit cette race de reload.
Il tient compte du modèle détecté avant l'appel et des budgets
16A lorsqu'ils sont présents. Une politique globale passerelle changée peut
invalider plus de résultats que strictement nécessaire : choix conservateur.

Les anciens fichiers restent intacts ; aucun fallback vers les anciennes clés
incomplètes et aucune purge. Un premier appel d'un **nouvel article** peut manquer
le cache après mise à jour ; les articles déjà publiés restent dédupliqués et ne
sont pas régénérés. Les checkpoints prepared existants se réconcilient sans
régénération : ils ne doivent pas servir à mesurer le nouveau prompt.

`cache_validator` est une extension optionnelle du client et de la passerelle,
compatible avec les signatures existantes. Elle contrôle lecture et écriture du
cache, y compris après attente du flock. Une réponse fraîche invalide revient au
caller pour diagnostic mais n'est pas cachée. Un ancien cache invalide est ignoré,
pas supprimé. Callback pure/bornée ; exception → refus de cache. Cache TTL, double
vérification sous verrou et libération du bail restent conservés.

Les caches de traduction sont eux aussi versionnés. La passerelle inclut la valeur
du cache_key caller dans son espace ; les autres tâches sans validator gardent leur
comportement. Pas de changement des providers, modèles, quotas ou fallback payant.

## Traces privées optionnelles

`trace.enabled: false` par défaut ; aucun répertoire créé. Activation explicite :
records indépendants `data/editorial_traces/<UUID>.json`, version 1, répertoire 0700,
fichiers 0600, verrou stable non bloquant, fsync et remplacement atomique. Pas d'API
ou export public ; tests Flask sur les routes publiques/historiques et traversées.

Une tentative peut contenir titre original, extraction (≤3500), entrée LLM (≤3000),
version/hash du prompt, configuration du modèle et paramètres connus, réponse
textuelle finale avant nettoyage, texte nettoyé, traduction, candidat d'article,
observations et échec typé. Source et réponse brute nécessitent en plus
include_source et include_raw_response. Rester dans les droits/règles de rétention
applicables ; aucun rapport réel dans Git ou les artifacts publics.

Durée mesurée au niveau ollama_call : **inclut attente et cache éventuel**, pas un
temps d'inférence garanti. Les tokens/finish_reason/cache_hit restent null quand
le contrat client ne les expose pas de manière attribuable. Le modèle est une
configuration de client avant appel, pas une attestation fournisseur ni une
empreinte des poids réellement chargés. Les hints model_revision/model_sha256 sont
déclaratifs. Ne pas rapprocher des lignes de log de #23 au hasard pour les remplir.
Une future corrélation explicite pourra compléter ces champs après intégration.
La PR #23 reste nécessaire pour retirer son retry HTTP aveugle : 16E ne le duplique pas.

Le champ pipeline_status est un snapshot de tentative en mémoire, pas un reçu
transactionnel de publication. final_article est le candidat final ; vérifier le
journal et le fichier articles pour prouver le commit. Une reprise prepared peut
ne pas avoir de nouvelle trace de génération. L'absence de trace ne prouve pas
l'absence de génération.

Rétention par défaut : 7 jours, 200 records, 16 MiB, 64 KiB par record. Options
bornées : 1–30 jours, 1–1000 records, 64 KiB–64 MiB quota, 4–128 KiB par record.
Nettoyage des anciennes traces lors des écritures, pas de daemon : si désactivé,
aucune purge ultérieure automatique. Une rétention calendaire stricte hors activité
nécessite maintenance opérateur. Les anciens articles/audio/caches ne sont jamais
concernés par cette rétention.

Liens symboliques/durs, fichiers spéciaux, propriétaire/permissions incorrects,
JSON corrompu ou fichier inconnu → préservation, refus de nouvelle trace et message
générique. Les motifs connus de secrets/raisonnement font refuser la trace entière,
sans prétendre détecter tout contenu sensible. Le transport ne transmet pas les
champs internes de raisonnement. Une mention technique littérale de ces marqueurs
peut aussi être refusée : limite conservatrice. Aucun contenu, chemin, header,
URL privée ou exception brute dans le log de trace.

Échec disque/quota/permissions/verrou occupé n'empêche pas le traitement normal.
Un temporaire abandonné après crash est préservé et peut désactiver de nouvelles
traces jusqu'à revue ; il ne bloque pas la publication. Toute maintenance doit
préserver archive.lock tant qu'un producteur peut l'utiliser. POSIX local requis,
pas de garantie contre un administrateur hostile remplaçant les répertoires parents.

## Validation et qualification

Tests de nettoyage, Unicode, cache/version/TTL/contrat invalide, gateway, titres,
reprise, strict/observe, traces/quota/corruption/permissions et non-exposition API.
Aucun benchmark Ornith/Qwen/Gemma ni gain qualitatif réel revendiqué à partir de
mocks. Aucun modèle gagnant. Voir [SPRINT_16E_FIELD_TEST.md](SPRINT_16E_FIELD_TEST.md)
pour la procédure opérateur et le rollback, sans exécution automatique.

## Proposition editorial-v2.1, non qualifiée sur modèle réel

Voir [EDITORIAL_V21_QUALIFICATION.md](EDITORIAL_V21_QUALIFICATION.md) : prompt court
à refus limité, extraction opt-in, corrélation par requête et corpus synthétique
pour trois prompts. Le défaut reste legacy-v1. Les tests de contrat ne remplacent
pas les mesures Ornith sur une instance dédiée autorisée ; aucun déploiement
atlas-lab n'est effectué.
