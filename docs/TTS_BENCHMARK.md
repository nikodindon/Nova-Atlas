# Laboratoire TTS — Sprint 10

## Livraison et périmètre des preuves

`modules/tts_lab` constitue un banc d'essai **séparé de la radio**. Aucun moteur
ne remplace Edge ; aucun prompt, endpoint Android, reçu MP3 ou mix de production
n'est modifié. Les dépendances lourdes sont optionnelles, hors locks/images de
production. Aucune voix clonée, aucun fichier audio personnel, aucun poids trouvé
sur la machine n'est utilisé automatiquement.

Adaptateurs livrés : **fixture sinusoïdale**, **Piper CPU** et **Edge réseau sous
activation explicite**. Les deux derniers sont testés par mocks, sans lancer de
modèle ni service réel pendant ce sprint. Kokoro, XTTS, F5 et Chatterbox sont
étudiés, sans installation ou adaptateur promis. Le banc minimal fonctionne sans
GPU, compte, téléchargement de poids ou nouveau package Python.

**Résultats réels de synthèse vocale : non mesurés. Écoutes : aucune.** Les seules
expériences exécutées produisent des tonalités synthétiques pour valider le banc.
Leur RTF n'est jamais présenté comme la performance d'un moteur TTS.

## Audit du pipeline radio existant

`bulletin_generator.py` : script complet → validation structurelle → balises
VOIX1/VOIX2/VOIX3/VOIX_BREAKING → manifeste ordonné → Edge → inspection MP3 →
concaténation → mix → MP3 et reçu publiés atomiquement. `audio_integrity.py`
exige un décodage complet ; les streamers principal et Android vérifient les reçus.
Les flashs manuels utilisent ce même pipeline et leurs contrôles restent bloquants.

- Voix configurées dans bulletins.yaml : Henri/Denise, RemyMultilingual pour
  breaking et Antoine pour culture. Le constructeur privilégie la liste par
  langue dans config.radio.voices et peut en tirer deux au hasard. Les essais
  du laboratoire choisissent des voix **fixes**, jamais de sélection aléatoire.
- Edge produit du MP3 ; timeout async par tentative 90 s, trois tentatives,
  backoff 1/2/4 s par défaut, paramètres bornés. Aucun segment ne peut être sauté.
- Un segment métier peut contenir plusieurs longues phrases. Le laboratoire
  étudie le redécoupage, mais ne le branche pas sur la production.
- Mix historique : gain voix 2, fond au volume configuré (0,30 par défaut),
  `amix`, MP3 contrôlé. Ce n'est pas une mesure LUFS ni une preuve de prononciation.
- Longueur éditoriale `warn` conservée ; les limites structurelles et audio
  restent strictes. Voir RADIO_INTEGRITY.md.

Le point d'extension existant `synthesizer(text, voice, path, timeout=…)` est
compatible avec un futur bridge. Les WAV du laboratoire ne satisfont **pas** les
reçus radio et ne doivent jamais être copiés dans audio_queue comme publications.

## Moteurs et licences examinés le 6 novembre 2026

Les sources officielles et décisions sont versionnées dans `catalog.json` et
reprises par le rapport généré. Une licence de code ne règle pas à elle seule
les droits sur des poids, des voix ou leur diffusion. L'opérateur conserve une
revue datée de la carte du modèle exact et de son origine, avant `license_reviewed`.

| Candidat | Code / poids / voix | Décision de ce sprint |
|---|---|---|
| Edge | Client LGPL-3.0 ; service Microsoft distant, poids/versions non disponibles ; Henri/Denise français | Baseline opt-in, pas de droit de radio Internet déduit de la licence du client |
| Piper | piper-tts 1.8.0 GPL-3.0-or-later ; carte siwis : français, un locuteur, 22 050 Hz, données CC BY 4.0 et origine lessac à examiner | Adaptateur CPU pour poids locaux épinglés ; première expérience CPU proposée |
| Kokoro | Code et carte Kokoro-82M sous Apache-2.0 ; liste officielle : ff_siwis, une voix française féminine | Candidat qualité compact à examiner ; pas de seconde voix FR inventée |
| XTTS | Toolkit fork Idiap MPL-2.0 ; XTTS-v2 CPML 1.0.0 limitant usage du modèle et sorties au non-commercial | Non retenu pour déploiement public sans revue appropriée ; aucun clonage |
| F5 | Code MIT ; poids préentraînés CC-BY-NC d'après le projet | Reporté : checkpoint français, références autorisées et droits non établis |
| Chatterbox | Code MIT ; dépôt de modèles MIT ; Multilingual V3 annoncé, français listé | Reporté : poids exacts, provenance des voix, ressources et watermark à examiner |

Sources : [Edge client](https://github.com/rany2/edge-tts),
[licence du client](https://github.com/rany2/edge-tts/blob/master/LICENSE),
[accord Microsoft](https://www.microsoft.com/en-us/servicesagreement),
[Piper](https://github.com/OHF-Voice/piper1-gpl),
[Piper 1.8.0](https://pypi.org/project/piper-tts/1.8.0/),
[carte siwis](https://huggingface.co/rhasspy/piper-voices/blob/main/fr/fr_FR/siwis/medium/MODEL_CARD),
[Kokoro code](https://github.com/hexgrad/kokoro),
[Kokoro poids](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/README.md),
[voix Kokoro](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md),
[Idiap TTS](https://github.com/idiap/coqui-ai-TTS),
[licence XTTS](https://huggingface.co/coqui/XTTS-v2/raw/main/LICENSE.txt),
[F5 officiel](https://github.com/SWivid/F5-TTS),
[Chatterbox](https://github.com/resemble-ai/chatterbox),
[carte de modèles Chatterbox](https://huggingface.co/ResembleAI/chatterbox/blob/main/README.md).

La maintenance observée (dépôts actifs, publication Piper récente, fork Idiap)
n'est pas une garantie de pérennité. Aucune conclusion sur l'ensemble des modèles
portant ces noms. Les conditions Microsoft ne constituent pas ici une autorisation
établie de rediffuser le service Edge : revoir les conditions exactes applicables
avec le propriétaire avant un choix de déploiement. Aucun appel Edge effectué.

## Corpus commun et découpage

`corpus/fr-v1.json` : douze textes originaux fictifs, sans données privées,
versionnés avec SHA256 global et par texte. Actualités, géopolitique, économie,
crypto/tech, noms russes/japonais/chinois/arabes/anglophones, nombres, expression,
50/150/400/800 mots et bulletin de 1500 mots. Les noms ne portent aucune
attribution de fonction actuelle ni déclaration réelle. Scénario long : une suite
de développements fictifs, pas une actualité ni un test représentatif de toute la
langue française. Ponctuation, paragraphes et chiffres restent identiques.

Le plan de segments conserve **exactement** tous les caractères : concaténer les
textes redonne l'entrée, dans l'ordre numéroté. Frontières de phrases/paragraphes
prioritaires, puis espaces ; mot indivisible trop long → refus, jamais troncature.
Plafond commun par défaut 600 caractères, 512 segments, 128 000 caractères.
Le réglage `--chunk-chars` est consigné. Une comparaison doit garder ce réglage
identique entre moteurs ; un essai à une autre taille constitue une autre expérience.
Pas de pauses synthétiques supplémentaires, pas de normalisation textuelle cachée.

Un segment absent, vide, invalide ou silencieux fait échouer toute l'expérience.
Les formats natifs sont conservés dans le répertoire privé, puis convertis en
**WAV PCM16 mono 24 kHz** par FFmpeg, protocole file/pipe seulement, format d'entrée
explicite MP3/WAV et un thread. Les WAV sont inspectés intégralement ; assemblage
dans l'ordre avec vérification du nombre total d'échantillons. Un second fichier
applique un gain RMS cible −20 dBFS avec plafond de pic 0,95. Ce gain simple n'est
**pas** une normalisation EBU R128, un mix musical ou un mastering de radio.

## Mesures

RTF = secondes de synthèse / durée audio ; <1 signifie plus rapide que le temps
réel. Le RTF de bout en bout est également conservé, conversion et contrôle inclus.
L'import/adaptation/chargement du modèle est mesuré séparément. Premier segment
après chargement et répétitions suivantes sont séparés ; pour reproduire un vrai
démarrage à froid, relancer dans un nouveau répertoire/processus. Le démarrage de
Python et la vérification des empreintes ne sont pas inclus dans le temps de chargement.

Par passage : texte/SHA, caractères/mots, temps, débit, durée, fréquence/canaux,
taille/SHA, RMS/pic, silence continu le plus long, segments, statut. Un silence
>2 s est signalé pour revue ; un simple contrôle acoustique ne démontre ni fin de
phrase complète, ni absence d'hallucination, répétition ou changement de langue.
`coverage: unverified_no_asr_or_listening` reste explicite. Aucun ASR activé.

RAM : ru_maxrss Linux du worker, maximum cumulatif, pas une mesure par segment
ni celle de FFmpeg et de tous ses descendants. CPU : process_time / durée du
passage, proportion d'un cœur ; pas la télémétrie de toute la machine. Les versions
Python/bibliothèques/outils sont consignées. VRAM et utilisation GPU restent nulles
si non mesurées, jamais zéro déduit. La fiche matérielle exclut hostname, utilisateur,
numéro de série et variables d'environnement. `--probe-gpu` interroge uniquement
l'inventaire nvidia-smi, pas un benchmark ni une preuve d'inférence CUDA.

Le tableau `docs/measurements/tts/COMPARISON.md` est généré depuis le catalogue et
les résultats fournis. Les fixtures sont exclues des performances TTS. Les lignes
par expérience conservent modèle/voix/CPU/mode pour éviter de confondre les machines.
Aucune note humaine n'est automatiquement produite.

## Exécution minimale et installation facultative

Depuis la racine du dépôt, avec l'environnement Python existant :

```sh
python -m modules.tts_lab run                         # dry-run, pas de configuration privée lue
python -m modules.tts_lab run --engine fixture --execute \
  --case longueur-50 --case longueur-800 --repetitions 3 \
  --output .tts-lab/experiences/fixture-001
python -m modules.tts_lab report .tts-lab/experiences/fixture-001/result.json
```

Un répertoire neuf est obligatoire. Chemins de production et symlinks refusés.
Les résultats sont privés, jamais publiés dans audio_queue. L'expérience échouée
conserve son rapport mais retire ses sorties audio partielles. Crash SIGKILL : un
répertoire privé peut rester ; ne jamais l'envoyer aux streamers. Aucune reprise
automatique ; relancer dans un nouveau répertoire.

Piper facultatif, après revue, **commandes opérateur non exécutées pendant le sprint** :

```sh
python3.12 -m venv .venv-tts-lab
.venv-tts-lab/bin/python -m pip install -r requirements-tts-lab.txt
.venv-tts-lab/bin/python -m pip freeze > .tts-lab/dependencies.lock
# L'opérateur choisit/télécharge séparément ONNX + JSON et carte de modèle,
# à une révision précise, dans un stockage dédié. Aucun downloader du lab.
# Calculer sha256sum des deux fichiers et compléter une copie privée du spec.
python -m modules.tts_lab run --engine piper --spec .tts-lab/piper-spec.json \
  --python .venv-tts-lab/bin/python --execute --repetitions 3 \
  --case longueur-50 --case longueur-150 --case longueur-400 \
  --output .tts-lab/experiences/piper-cpu-001
```

Créer `.tts-lab/` privé avant d'y écrire le lock. Le pin direct et les versions
observées ne remplacent pas un lock transitif de l'environnement réel ; conserver
ce lock avec les mesures. Aucun poids n'a été choisi ou installé par l'agent.
Modèles/config vérifiés par SHA256 avant/après exécution ; ne pas modifier leurs
fichiers pendant une expérience. Deux modèles monolocuteurs distincts peuvent
être essayés séparément, sans fabriquer une seconde voix d'un modèle unique.

Spécimens non opérationnels : `piper-spec.example.json`, `edge-spec.example.json`.
Les placeholders et `license_reviewed: false` provoquent un refus sûr. L'ID modèle,
la révision et la voix sont exigés ; les chemins privés ne sont pas copiés au rapport.
Piper : CPUExecutionProvider et un thread intra/inter ONNX, poids <=512 MiB et
config <=64 KiB. Paramètre optionnel length_scale borné 0,5–2. Edge : Henri/Denise
uniquement, débit optionnel ±30 %, versions client contrôlées. Une adaptation de
paramètres doit être comparée et documentée, pas cachée dans le texte.

Edge nécessite **en plus** `--allow-network`, `--execute`, un spec et la revue des
conditions. Le service ne publie pas de révision de modèle : `remote-unversioned`
est un constat, pas une garantie de reproductibilité. Aucun secret requis par le
client actuel, aucun envoi réseau réalisé dans les essais de cette PR.

Linux uniquement pour les limites : worker nice +10, affinité initiale un CPU,
2 GiB d'espace d'adressage, 300 s CPU, timeout global <=300 s (120 par défaut),
32 MiB/fichier, environ 256 MiB d'audio/expérience. Conversion <=15 s/segment.
SIGINT/SIGTERM et annulation entraînent arrêt du groupe de processus, puis kill
après deux secondes si nécessaire, nettoyage et rapport fixe. Les bibliothèques
peuvent nécessiter davantage d'espace virtuel : un refus n'établit pas une RAM
requise précise du modèle. Aucun routage GPU disponible dans cette première tranche.

L'environnement enfant ne reçoit ni credentials ni proxy hérité. Piper/fixture
bloquent les connexions Python ; chemins locaux seulement, offline Hugging Face,
aucun téléchargement. Ce n'est pas une sandbox d'un modèle/binaire malveillant :
n'utiliser que des poids/dépendances de confiance. CI ajoute network=none au smoke.

## Écoute à l'aveugle

Si de véritables échantillons existent :

```sh
python -m modules.tts_lab blind .tts-lab/experiences/piper-cpu-001/result.json \
  --output .tts-lab/ecoute/session-001 --seed 10
# Écouter les WAV E001… ; garder .private-key.json inaccessible à l'auditeur.
python -m modules.tts_lab validate-listening .tts-lab/ecoute/session-001/listening-form.json
```

Le formulaire conserve notes 1–5 et commentaire : naturel, prononciation,
intelligibilité, fluidité, intonation, rythme, ponctuation, timbre, expressivité,
confort, chiffres/acronymes, cohérence. Champs omissions/répétitions/langue et
pseudonyme d'auditeur. Notes nulles tant qu'aucune écoute n'a eu lieu ; les fixtures
sont refusées pour une évaluation vocale. Un auditeur et quelques extraits ne
forment pas un MOS scientifique. Conserver formulaire rempli et clé privés, partager
uniquement une synthèse relue et consentie. Pas d'agrégateur de notes branché au
classement automatique dans cette première version.

## Résultats livrés et protocole Ryzen/i5

`fixture-cpu.json` : quinze passages (cinq longueurs × trois répétitions), réels
chronométrages du **banc à tonalités**, sur CPU Ryzen 5 1600, environ 32 GiB RAM,
Linux/Python 3.12 ; affinité un cœur. La correspondance `profile_match=A` est un
rapprochement du **nom CPU seulement**, sans preuve du suffixe AF ni de la carte
GTX déclarée. Aucune mesure TTS, GPU, machine B ou écoute. Le fichier brut conserve
les durées précises, tailles, RAM et versions ; aucun WAV/MP3 ou poids commité.

Pour A (Ryzen/32 GiB/GTX1650S4GiB) et B (i5/16 GiB/GTX1050mobile4GiB) :

1. Conserver les caractéristiques déclarées de `profiles.json` distinctes des
   observations. Vérifier Linux, RAM, GPU/pilote, dépendances et modèle exact.
2. Prévoir une fenêtre courte sans gêner les services ; ne rien arrêter pour le
   benchmark. Commencer par 50 mots, CPU, trois passages, observer la charge.
3. Relancer le processus pour plusieurs démarrages à froid ; élargir à 150/400,
   puis 800/1500 uniquement si budgets, santé machine et licence le permettent.
4. Conserver specs, lock et rapports, sans secrets. Comparer natifs et PCM normalisés,
   deux voix seulement si elles existent, volumes et transitions à l'écoute.
5. Un essai GPU futur exige un adaptateur explicite et télémétrie testée, pilote
   compatible et mesure de pic réel. **Aucune promesse de compatibilité 4 GiB ni
   d'offloading** à partir de la taille des poids.
6. Vérifier les fins de phrase, langue, répétitions, nombres, confort prolongé et
   totalité des textes. ASR futur opt-in uniquement, jamais une preuve parfaite.

## Recommandation et Sprint 11

- **CPU :** commencer les mesures avec Piper monolocuteur français, pour son
  interface locale et sa séparation simple des modèles ; ce n'est pas un vainqueur
  mesuré ni une déclaration de droits de diffusion déjà acquis.
- **Qualité :** examiner Kokoro après qualification des poids/voix, puis éventuellement
  Chatterbox. Pas de classement auditif ni candidat GPU qualifié actuellement.
- **Secours :** Piper pourrait fournir une disponibilité hors réseau une fois testé ;
  aucun secours nouveau activé ou qualifié dans la radio actuelle.
- **Edge :** conserver le comportement actuel pendant ce sprint. Décision de
  remplacement suspendue aux mesures CPU, à l'écoute et à la revue des conditions.

Sprint 11 devra relier le choix de profil bulletin/flash/annonce à un bridge qui
retourne un MP3 décodable sous timeout, en zone privée. Aucun accès direct à la
publication ; manifeste intégral, contrôles et reçus restent autoritaires. Fallback
explicite, journalisé, sans substitution silencieuse de voix. Un échec reste bloquant.

Rollback : retirer les commandes/dépendances du lab ; aucun chemin de production
n'y fait appel. Conserver ou supprimer explicitement les seules expériences privées,
jamais les modèles déjà installés, les MP3 de radio ou les fichiers historiques.
