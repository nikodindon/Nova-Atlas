# Qualification éditoriale des LLM — complément au Sprint 16

Outillage autonome depuis dev : aucun changement des prompts de production,
aucun appel HTTP/LLM, aucune gestion de conteneur, aucun upload. Les PR #22 et #23
restent indépendantes. Ce dossier ne qualifie aucun nouveau modèle en exploitation.

## Ce qui est récupérable

Les articles JSON, le cache de résumés, les MP3/reçus et le journal Radio Studio
n'ont pas les mêmes garanties de provenance. L'outil distingue une sortie stockée
d'une réponse brute attribuable. Lorsqu'aucun script complet attribuable n'est
conservé, il indique zéro script récupéré : **ne jamais reconstituer un script
supposé exact depuis un MP3**. Aucune transcription ASR n'est effectuée.

Les résultats d'inspection de l'installation et les exemples réels restent dans
les rapports locaux privés. Cette PR ne contient ni ces exemples, ni leurs
citations, ni l'inventaire privé des modèles. Les limites ci-dessous décrivent les
contrats historiques visibles dans le code. La sélection par catégorie est non
aléatoire et ne représente pas toute l'activité d'un modèle.

Limites de provenance :

- `summary` est la **sortie persistée après nettoyage**, pas nécessairement les
  octets bruts de la réponse HTTP. Le cache utilise une identité dépendant d'un
  préfixe de contenu non conservé ; ne pas deviner un lien cache/article.
- Le modèle/paramètres lus dans config sont une observation actuelle, pas une
  preuve de l'identité de chaque génération passée. Aucun accès aux fichiers de
  secrets. Seuls provider connu, basename du modèle et paramètres numériques
  autorisés sont projetés ; ni endpoint ni chemins locaux complets.
- Le hash de la fonction `_summarize` du checkout est un **candidat de référence
  actuel**, pas le prompt historique prouvé. Pas de reconstruction du texte source
  ni du prompt exact : l'entrée d'extraction de 3000 caractères n'est pas conservée.
- Durée, tokens et finish_reason absents historiquement restent null. Les futures
  observations de #23 ne doivent pas être rattachées à un article au hasard.
- Les dates brutes sont préservées sans conversion de fuseau supposée.
- Une photographie de chaque fichier est contrôlée, pas une transaction globale
  de tous les fichiers actifs. En cas de changement pendant lecture, relancer.

La revue distingue style, cohérence titre/résumé, qualité du français et fidélité.
Les défauts d'une sortie nettoyée ne sont pas attribués automatiquement au modèle :
extraction, traduction, cache et nettoyages doivent être examinés. La véracité et
les informations perdues ne sont **pas évaluables de manière fiable** sans source
ou référence factuelle. Aucun score de vérité automatique ni gagnant multi-modèles.

## Export en lecture seule

Depuis un checkout séparé contenant cet outil, avec Python 3.12 et les dépendances
existantes (aucun usage de la venv opérateur ni modification du déploiement) :

```bash
python -m deploy.editorial_qualification export \
  --state "$HOME/atlas-lab/state" \
  --repo "$HOME/atlas-lab/Nova-Atlas" \
  --count 20 \
  --output "$HOME/atlas-lab/editorial-qualification-NOUVELLE-DATE"
```

La destination doit être neuve, avoir un parent existant, être disjointe de state
et du checkout, et située **hors de tout worktree Git**. Le programme crée seulement
ce répertoire : mode 0700, fichiers 0600, fsync, publication atomique sans remplacement.
Il ne crée pas de verrou dans l'installation et ne lit pas les gros modèles/audio.
Les noms des fichiers produits sont fixes ; aucun chemin tiré d'une URL.

- `private.json` et `private.md` : textes stockés, métadonnées autorisées, empreintes
  d'entrée, limites et inventaire radio. Pas d'article source intégral ni de champ
  arbitraire du JSON original.
- `review.json` : grille de revue de confidentialité/droits par identifiant, liée
  au SHA-256 exact du rapport privé.
- `share-draft.json` et `share-draft.md` : **contenu retenu**, pas une publication des
  20 résumés sans revue préalable.

Bornes : 15…20 exemples, 366 fichiers plats `data/articles/*_articles.json` au plus,
8 MiB par fichier, 64 MiB au total, 20 000 lignes par fichier ; cache : 2000 textes,
256 KiB chacun, 64 MiB au total. Un fichier corrompu/instable n'est pas silencieusement
omis ; l'export échoue sans modifier les originaux. Les enregistrements invalides
sont comptés ; moins de 15 exemples valides refuse l'export. La sélection déduplique
l'identité historique, puis parcourt les catégories à tour de rôle depuis les
fichiers les plus récents. Les historiques sous d'autres layouts nécessitent un
adaptateur explicite ; pas de scan arbitraire du système.

Fichiers réguliers uniquement, refus des symlinks/hardlinks/fichiers spéciaux,
lecture bornée et recontrôle fstat. Ces contrôles supposent un stockage POSIX local
et des répertoires de confiance ; pas de promesse contre un administrateur hostile
remplaçant les répertoires parents pendant l'opération.

## Partage vers GitHub : revue puis accord distinct

1. L'opérateur examine le rapport privé. Les secrets connus et balises de
   raisonnement courantes sont masqués ; le filtre n'est **pas exhaustif**.
2. Dans une copie privée de `review.json`, sélectionner uniquement les cas dont
   la confidentialité **et les droits sur la sortie** ont été examinés. Renseigner
   `privacy_reviewed: true`, `output_rights_reviewed: true` et `review_note`.
   L'accessibilité d'un article ne vaut pas autorisation ; un résumé peut aussi
   reprendre des passages protégés. Une case cochée n'est pas une vérification juridique.
3. Préparer une nouvelle projection locale :

```bash
python -m deploy.editorial_qualification share \
  --private /CHEMIN/PRIVE/private.json \
  --review /CHEMIN/PRIVE/review-operateur.json \
  --output /CHEMIN/NEUF/rapport-partageable
```

Les URL publiques perdent query/fragment ; URL avec credentials ou schéma dangereux
refusées. Emails et motifs connus de secrets masqués. Pas de prompt contenant le
texte source, de paramètres privés ni de métadonnées inconnues. Le Markdown place
les textes externes dans des blocs inertes, sans hotlink ni HTML actif. Contrôler
encore les noms personnels, citations, liens et droits non détectables par regex.

4. **Demander l'accord explicite de publication** sur la sélection relue et le
   dépôt destinataire. Même après la projection, `publication_authorized` reste
   false : l'outil ne possède aucune commande d'upload. Ne pas confondre droit de
   lire une instance et autorisation de publier ses données.
5. Seulement après accord, ajouter le rapport approuvé dans une PR de revue dédiée.
   ChatGPT pourra alors ouvrir le fichier GitHub et citer les identifiants de cas.
   Jamais d'upload automatique d'export privé ou d'artifact CI contenant l'instance.

La PR d'outillage ne contient que code, documentation et corpus fictif écrit pour
les tests. Les rapports réels doivent rester hors Git jusqu'à accord distinct.

## Benchmark multi-modèles hors ligne

Les fiches officielles consultées le 10 novembre 2026 confirment l'existence des
familles demandées :

| Candidat officiel | Vérification opérateur requise |
|---|---|
| [Ornith 1.5 35B-A3B](https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B) | Révision, empreinte, quantification et correspondance avec les poids officiels à établir ; un nom de fichier ne suffit pas |
| [Qwen3.6 35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) | Ne pas confondre le modèle officiel et un dérivé Uncensored ; vérifier le fournisseur exact de la quantification |
| [Gemma 4 26B-A4B-it](https://huggingface.co/google/gemma-4-26B-A4B-it) | Vérifier la présence de poids compatibles et leur template avant de prévoir une exécution |

Les pages évoquent les quantifications/llama.cpp, ce qui ne prouve pas que le
binaire installé accepte chaque architecture/template. Le nombre de paramètres
actifs d'un MoE n'est pas l'empreinte mémoire de tous ses poids. Aucun chargement,
hash de fichiers de dizaines de Go, téléchargement ou changement du serveur en
cours. Compatibilité CPU/GPU/RAM/VRAM et qualité : **non testées**.

Avant un essai réel, sur environnement **réellement indépendant de la radio** :
relever révision exacte et SHA des poids, quantification, build llama.cpp/template,
réglage explicite du raisonnement, contexte, threads, offload, RAM/VRAM, licence
exacte de l'artefact. Vérifier un court appel de contrat avec ressources réservées,
puis seulement une campagne autorisée. Ne pas copier les commandes de téléchargement
implicite des fiches modèles. Pas de modèle local remplacé pendant une émission.

L'outil fourni **planifie et analyse** des captures normalisées ; il n'est pas un
exécuteur d'inférence. Mode minimal utilisable sans modèle ni réseau :

```bash
python -m deploy.editorial_benchmark plan \
  --corpus config/editorial_qualification/corpus.synthetic.json \
  --models config/editorial_qualification/models.example.json \
  --repetitions 3 --output /RAPPORTS/NEUF/plan
python -m deploy.editorial_benchmark compare \
  --plan /RAPPORTS/NEUF/plan/plan.json \
  --runs /RAPPORTS/PRIVE/runs.json --output /RAPPORTS/NEUF/comparaison
```

Chaque cas définit id, task summary/radio, prompt exact, prompt_version,
reference_excerpt (≤2000 caractères) et reference_status. Les mêmes octets sont
utilisés pour chaque modèle ; prompt et paramètres sont empreintés. Le corpus
fourni est **fictif**, pas une réécriture des articles réels. Pour une vraie campagne,
préparer en privé les mêmes entrées autorisées et une référence annotée ; ne pas
retélécharger automatiquement des articles en CI. Ne jamais substituer le résumé
d'un modèle à la référence factuelle.

Plan : température 0,3, seed 42, plafond 4096, timeout prévu 300 s ; ces paramètres
sont une proposition de contrôle, pas des valeurs exécutées ni optimisées pour
chaque modèle. Le serveur de benchmark externe doit effectivement les appliquer,
consigner le mode de raisonnement et respecter ces délais. Les tokens ne sont pas
équivalents entre tokenizers ; le nombre de mots reste un indicateur complémentaire.
Même seed ne garantit pas une identité bit à bit entre runtimes.

### Captures à importer

```json
{
  "version": 1,
  "kind": "fixture_replay",
  "plan_sha256": "SHA256_DES_OCTETS_DE_PLAN_JSON",
  "runs": [{
    "case_id": "summary-a", "model_id": "ornith-15-35b", "repetition": 1,
    "prompt_sha256": "SHA256_DU_PROMPT_UTF8",
    "parameters_sha256": "EMPREINTE_PRESENTE_DANS_LE_PLAN",
    "output": "Texte final uniquement, jamais le raisonnement",
    "duration_ms": null, "prompt_tokens": null, "completion_tokens": null,
    "finish_reason": "stop", "error": null
  }]
}
```

`fixture_replay` identifie explicitement les timings simulés, jamais des mesures
réelles. `operator_measurement` exige dans le plan révision, quantification, SHA
des poids, runtime, matériel, reasoning_mode on/off identique et compatibility=operator_validated ; déclaration
opérateur, pas preuve que l'outil a lui-même exécuté le modèle. Les champs inconnus,
y compris reasoning_content, sont refusés. Error : null/timeout/http_error/empty/
invalid_response/cancelled ; finish_reason : stop/length/unknown. Aucun message
fournisseur arbitraire. Captures ≥3 répétitions conseillées ; 1…5 autorisées.

Le comparateur refuse identités/entrées modifiées et doublons. Il conserve les
absences comme runs manquants, jamais succès ; produit longueur, tags de voix,
répétitions exactes de phrases, durée disponible, tokens disponibles, échecs et
troncatures. Les répétitions de phrases ne sont pas un score de style ou de vérité.
Aucune recommandation automatique du meilleur modèle. Les sorties restent privées.

### Grille humaine

`human-review.json` fournit cinq critères à noter 0…4 ou laisser null : fidélité,
faits essentiels conservés, français, répétitions/style, instructions/balises.
Pour chaque note : exemple exact de sortie, élément de référence, explication,
évaluateur et cas. Exemple synthétique : source « deux bus » → sortie « trois bus »
justifie une erreur numérique ; l'exemple n'est pas une mesure sur Ornith.
Sans source suffisante, fidélité reste null. Une incohérence titre/résumé justifie
une investigation d'extraction, pas une condamnation factuelle automatique.
Comparer à l'aveugle si possible ; ne pas appeler une appréciation ChatGPT une
vérité terrain indépendante. Les notes libres ne sont pas automatiquement agrégées.

Tester variantes de prompt A/B à modèle constant, puis modèles à prompt constant.
Comparer version brute et post-traitée avant de modifier les nettoyages. Propositions
à mesurer : absence de préambule, longueur adaptée à la quantité de faits, maintien
des nombres/attributions/incertitudes, absence de citations inventées. Aucun prompt
éditorial de production changé par cette PR.

## Instrumentation minimale prévue dans 16B, non activée ici

Après fusion opérateur de 16A, l'assemblage déterministe radio sera le point naturel
pour archiver le script **après validation et avant synthèse**. Contrat proposé :

- Option `bulletins.script_archive.enabled: false` ; chemin privé sous data,
  séparé de audio_queue, non servi par Flask ni les exports publics.
- Un UUID de génération et le job/slot Europe/Paris, type bulletin/flash,
  created_at UTC, contexte éditorial ciblé (pas heure de lecture supposée).
- Script final exact, SHA UTF-8 et **liste ordonnée des segments effectivement
  transmis au synthétiseur** : index, balise, voix, texte exact, nombre de mots.
  Stocker aussi le segment normalisé si une conversion précède le TTS ; ne pas
  qualifier le script pré-nettoyage de texte exact prononcé.
- Version de prompt explicite et hash du prompt rendu, articles/hash de provenance,
  phrases fixes choisies et paramètres effectifs (modèle/alias, quantification et
  SHA des poids si connus, température, plafond, langue). Pas de raisonnement,
  de corps fournisseur, d'URL d'administration ni de clé.
- Métadonnées liées à un generation_id : durée, tokens et finish_reason lorsqu'ils
  existent. L'observer LLM optionnel transmet des champs sûrs, pas une recherche
  approximative dans les logs ; cache hit signalé et métriques inconnues null.
- Écriture atomique 0600 dans répertoire 0700, verrou POSIX stable indépendant ;
  aucune suppression/recréation de verrou. Délais d'admission courts/non bloquants.
- Rétention proposée configurable : 7 jours, 50 scripts, quota 16 MiB ; taille par
  script bornée. Nettoyage sous verrou, sans suivre les liens ; répertoire corrompu
  préservé et signalé, pas écrasé. Ne pas supprimer les publications/reçus.
- Archivage best effort : exception disque/permissions/quota ne refuse ni le script
  validé ni le bulletin. Logs : code d'échec et ID uniquement, jamais le texte.
- Une archive avant TTS signifie « script préparé », pas « audio publié » ; lier
  ensuite au reçu par job/génération, en gardant explicite un échec de synthèse.

L'outil actuel signale l'absence d'archives attribuables ; leur futur schéma/import
sera livré et testé avec le hook 16B, sans annoncer ici un archivage déjà actif.
Pour les résumés, prévoir séparément un échantillonnage optionnel brut/après-nettoyage
avec generation_id, source limitée autorisée et version du prompt : pas de conservation
indéfinie de tous les articles et pas de modification silencieuse de #23.

## Tests, limites et rollback

Tests synthétiques : export, conservation des octets sources, modes de fichiers,
corruption, liens dangereux, liens symboliques/durs et FIFO, limites, revue/version,
Markdown inerte, absence de scripts inventés, comparaisons identiques, runs manquants,
mesures simulées et absence de score automatique. Les suites existantes et smokes
sont conservés. Aucun benchmark lourd ou production radio sollicité.

Rollback : cesser d'utiliser les CLI ; aucun état ni configuration à migrer. Les
rapports créés sont des fichiers privés opérateur, à sauvegarder/chiffrer et purger
explicitement selon son choix. Aucune commande de suppression ou upload fournie.
