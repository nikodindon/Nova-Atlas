# Intégrité des bulletins radio — Sprint 01F

Socle : `dev@2db598d`, après fusion explicitement demandée de la PR #7 dont les
contrôles étaient réussis. Aucun service de diffusion, Edge TTS, collecte ou LLM
réel n'a été lancé. Aucun fichier de production n'a été modifié par les essais.

## Défauts reproduits

Six tests écrits et exécutés avant correction échouaient : segment manquant en
première, deuxième ou dernière position, structure invalide acceptée, mix dans la
file publique avant validation, noms identiques pour deux générations à la seconde.
La caractérisation qui attendait un segment silencieusement sauté est remplacée
par le refus de tout le bulletin. L'ancien test de pipeline sans assertion de
publication utilise désormais des fichiers temporaires et vérifie réellement le
résultat. Les données des tests unitaires sont fictives ; les codecs sont simulés.

## Script et manifeste

`split_script_by_voice()` bloque les balises inconnues, crochets malformés, segments
vides ou sans texte parlé, balises consécutives sans texte et balise finale vide.
Les balises admises restent `[VOIX1]`, `[VOIX2]`, `[VOIX3]`, `[VOIX_BREAKING]`.
Le texte initial sans balise conserve son affectation historique à VOIX1. Un
script avec une seule voix est structurellement utilisable ; les prompts multivoix
existants restent inchangés. Bornes : 128 000 caractères et 256 segments.

Avant le premier appel TTS, un manifeste interne `Segment` contient, pour chaque
index : balise, texte complet, voix sélectionnée, fichier privé, état, nombre de
tentatives et métadonnées audio. Toutes les correspondances de voix doivent être
présentes et non vides : aucun fallback silencieux sur une balise inconnue.
États : `pending`, `generating`, `failed`, `valid`. L'ordre du manifeste est celui
du script et celui du concaténateur. Le nombre de segments valides doit être
exactement celui des segments attendus.

Les erreurs structurelles sont toujours bloquantes. `validate_bulletin()` conserve
son retour `(ok, nombre_de_mots, message)` et distingue ensuite la longueur.
Options facultatives de `config/bulletins.yaml` :

```yaml
length_policy: warn             # défaut : conserve tout le texte, signale l'écart
# length_policy: strict         # option : refuse hors cible ± tolérance
flash_length_policy: warn       # conserve le parcours flash historique
flash_target_words: 750
flash_tolerance_words: 100
tts_max_attempts: 3
tts_timeout_seconds: 90
tts_retry_backoff: 1
tts_retry_max_backoff: 4
```

La cible et la tolérance des bulletins restent `target_words` et `tolerance_words`.
Le défaut `warn` préserve la compatibilité éditoriale historique : les sorties
LLM de 600 à 900 mots documentées dans `config/bulletins.yaml` restent publiables
si tous les contrôles structurels et audio réussissent, malgré la cible de 1 500
mots et sa tolérance de 250. Un écart de longueur est journalisé sans tronquer le
texte, y compris pour un script trop long. L'option explicite `strict` refuse un
bulletin hors cible ± tolérance avant tout appel TTS. Les limites structurelles restent applicables dans les deux cas.
Les flashs Flask utilisent le profil `flash`, sans changer leur prompt, leurs
endpoints ni la durée éditoriale cible. Leur défaut `warn` conserve les scripts
utilisables malgré un écart de longueur. L'ancienne troncature automatique disparaît.

## TTS et assemblage

Chaque segment dispose de trois tentatives par défaut (configuration bornée 1–5),
un timeout de 90 secondes (maximum 300) et un backoff exponentiel plafonné à quatre
secondes (maximum configurable 30). Une tentative échouée supprime uniquement son
fichier partiel. Les segments déjà validés restent dans le répertoire privé de la
même génération ; ils ne sont pas resynthétisés. Aucun segment obligatoire ne peut
être sauté : après épuisement du budget, `build()` retourne `None` et ne publie rien.

Le timeout Edge TTS enveloppe la coroutine de sauvegarde avec `asyncio.wait_for` ;
son annulation est testée sans réseau. L'interface injectable `synthesizer(text,
voice, path, timeout=...)` permet un adaptateur futur, sans implémenter un fallback
ni changer de moteur. Tout futur adaptateur doit respecter le timeout et annuler
ses écritures avant retour ; le pipeline n'abandonne pas un thread qui continuerait
à écrire dans un fichier prétendument final.

Pour chaque segment, puis le concaténé et le mix final :

- fichier régulier, non vide, non symlink, taille maximale 256 Mio ;
- décodage MP3 complet par FFmpeg, code retour vérifié, erreurs de décodage
  bloquantes, durée effectivement décodée positive ;
- si FFprobe est disponible : un unique flux MP3 audio, durée finie positive,
  fréquence 8–192 kHz et un ou deux canaux ; écart probe/décodage limité à 0,25 s ;
- sans FFprobe : FFmpeg reste obligatoire et fournit la durée via sa progression,
  mais les caractéristiques de fréquence/canaux ne sont pas disponibles ;
- formats des segments identiques lorsque connus ; concaténé comparé à la somme
  des durées, tolérance de padding MP3 `0,25 + 0,05 × nombre de segments` secondes ;
- mix comparé au concaténé (tolérance 0,30 s), sortie toujours MP3. Les paramètres
  existants du mix et le chemin sans musique sont conservés.

Concaténation/mix ont un timeout de 120 secondes ; FFprobe 15 secondes et le
contrôle de décodage 120 secondes. Les codes retour et sorties sont vérifiés,
les fichiers partiels nettoyés et la liste concat supprimée en `finally`. Les
outils de génération/validation n'acceptent que les protocoles locaux `file,pipe`,
jamais une URL réseau cachée dans un fichier. Les stderr ne sont pas journalisés
ni accumulés en mémoire ; aucun chemin privé ou texte du script n'est nécessaire
aux messages d'erreur. Les logs TTS donnent index et tentative, sans credential.

## Publication atomique

La génération utilise `audio_queue/.bulletin-<identifiant temporaire>/`, répertoire
privé 0700 créé par `TemporaryDirectory`, sur **le même système de fichiers** que
la destination. Les segments, la liste concat, la voix et le mix restent dedans.
Le répertoire temporaire configuré antérieurement reste accepté mais n'est plus
utilisé pour publier par déplacement inter-filesystems.

Après toutes les validations, le nom définitif est
`bulletin_YYYYMMDD_HHMMSS_<uuid32>.mp3`. Un reçu privé `.mp3.ready.json` contient
version, nom, taille, SHA-256, durée, codec et nombres attendu/validé de segments ;
il ne contient ni texte ni chemin privé. Sous un verrou local `.publication.lock`,
le pipeline vérifie l'absence de collision, synchronise le MP3 et le reçu, publie
le reçu, puis déplace atomiquement le MP3 avec `os.replace` et synchronise le
répertoire. Aucun ancien nom existant n'est remplacé, même en cas de collision
forcée dans les tests. La génération n'est pas sérialisée par ce verrou court.

Un reçu peut brièvement exister sans MP3 : tous les consommateurs l'ignorent.
Le MP3 définitif apparaît uniquement complet, déjà validé, avec son reçu. Aucun
`.part`, segment ou fichier de travail n'est ajouté à une file. Un échec avant le
déplacement ne modifie pas les publications précédentes. Après déplacement, un
échec de fsync peut laisser une publication complète sans notification : son reçu
permet de la reconnaître, sans supposer que le job a été diffusé.

## Consommateurs et compatibilité

`Streamer.enqueue_bulletin()` et `AndroidStreamer.enqueue_bulletin()` refusent un
nom non définitif, un reçu absent/invalide, une taille/empreinte différente ou un
compte de segments incohérent. Aucun FFmpeg n'est lancé par l'admission : le reçu
atteste les contrôles déjà exécutés, et l'empreinte vérifie les octets. Les octets
sont revérifiés avant lecture au prochain passage de diffusion.

Chaque consommateur garde un historique borné des 4 096 dernières publications
admises, protégé par un verrou de thread. Un même fichier ne peut pas être admis
deux fois dans cet historique. La boucle intentionnelle d'Android sur son bulletin
courant reste inchangée ; ce n'est pas une nouvelle admission.

`main.run_radio()` met d'abord en cache le fichier Android, puis l'ajoute à la file
principale : la radio principale ne peut plus supprimer le fichier avant la copie
Android. La copie Android et son reçu sont eux-mêmes publiés atomiquement dans
`android_cache/`. Un échec de copie conserve le bulletin courant ; aucun fallback
vers l'original éphémère. Au redémarrage, Android ne reprend que des publications
validées présentes dans la file ou son cache. Les anciens MP3 sans reçu sont
**conservés sur disque mais ignorés**, car leur intégrité ne peut être prouvée.

Les flashs manuels ne deviennent `done` que pour une publication validée. La route
administrative audio revérifie le reçu avant de servir le MP3. Les endpoints et
contrats Android/Icecast restent inchangés. La playlist, le heartbeat, FFmpeg de
diffusion et les mécanismes de supervision ne sont pas refondus. Un échec de build
ne demande pas de fade et ne met rien en file ; la musique continue normalement.

## Interruptions, nettoyage et rollback

| Cas | Résultat |
|---|---|
| Edge TTS échoue une fois | Retry du seul segment, fichiers réussis conservés |
| Budget TTS épuisé | Bulletin refusé, aucun MP3 public ajouté |
| Interruption entre TTS et concat / pendant le mix | Nettoyage par contexte pour exceptions et arrêt coopératif ; aucun fichier public partiel |
| Crash brutal avant publication | Travail caché abandonné, éventuellement reçu orphelin sans MP3 ; jamais admissible |
| Crash juste après publication | MP3 complet et reçu utilisables ; pas de reprise durable de la notification principale |
| Fichier modifié après admission | Refus au contrôle avant lecture, conservation du flux de secours existant |
| Ancien travail temporaire | Jamais repris ni diffusé automatiquement |

Après arrêt habituel de **tous les générateurs**, l'opérateur peut inspecter puis
supprimer uniquement les répertoires `.bulletin-*`/`.copy-*` abandonnés et les reçus
orphelins sans MP3. Ne pas nettoyer par âge pendant une génération : une génération
longue pourrait être active. Ne jamais supprimer `.publication.lock` en cours
d'utilisation. Aucune purge automatique de MP3, cache historique ou données réelles
n'est ajoutée par ce sprint. Les sauvegardes habituelles doivent inclure MP3 **et**
reçus ; ne pas publier leurs contenus dans un ticket.

Rollback : arrêter les générateurs, conserver les MP3/reçus et revenir au code
précédent ; les noms gardent le préfixe `bulletin_` et le suffixe `.mp3`, et les
anciens streamers ignorent les reçus. Le rollback réintroduit les défauts corrigés.
Aucune conversion des anciens fichiers ni action de déploiement n'a été exécutée.

## Limites

Les garanties supposent un stockage Linux local compatible flock/rename/fsync et
un utilisateur de service partagé entre génération/Web/streamers : les MP3 et reçus
nouveaux sont privés (0600). Le reçu protège contre les fichiers partiels ou
modifiés accidentellement ; ce n'est pas une signature contre un attaquant ayant
le droit de réécrire à la fois audio et reçu. Une modification externe pendant la
lecture ou un stockage défaillant ne sont pas couverts.

La validation prouve un audio décodable et un assemblage cohérent, pas que le TTS
a prononcé fidèlement chaque mot ou qu'un texte est éditorialement exact. Les
écarts de padding ont les tolérances indiquées. Aucun test acoustique de voix.
L'annulation async reste soumise au bon comportement de la bibliothèque et du
système réseau ; aucune préemption brutale d'un processus utilisateur.

Le manifeste et les admissions sont en mémoire : pas de reprise de segments après
redémarrage, pas d'exactly-once distribué, pas de déduplication sémantique entre deux
générations distinctes du même script. L'historique d'admission est borné, et Android
peut reprendre son dernier bulletin validé au démarrage. Les jobs PostgreSQL et la
politique de rétention automatique du cache restent à une étape ultérieure.

## Orchestration facultative Sprint 11

Radio Studio réserve le nom final avant build, sans changer MP3/reçu ni leurs
contrôles. Les observers ne stockent que métriques/voix et motifs typés. Le bridge
facultatif produit un MP3 de segment privé et revalide son décodage ; ni corpus,
benchmarks, Piper non qualifié ou fixtures marquées ne deviennent un runtime actif.
Length warn et la conservation de tous les segments restent identiques.

En Studio, les publications sont conservées après passage principal pour reprise.
En mode historique, suppression uniquement après succès local observé, plus après
un échec de transmission. Ce succès ne prouve pas la réception Icecast. Les
processus possédés sont nettoyés et les reconnexions bornées. La durée maximale
optionnelle d'inspection permet au bridge de partager son budget avec la validation,
sans supprimer le décodage complet. Voir RADIO_STUDIO.md pour les observations,
les changements de comportement et la différence intégrité/diffusion effective.
