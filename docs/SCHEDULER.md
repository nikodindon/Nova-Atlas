# Planification et arrêt — Sprint 01C

## Comportement corrigé

`main.run_news_engine` utilisait une boucle sans attente, un dernier créneau de
fetch sans date, des noms de threads variables selon le créneau et un historique
d'éditions croissant indéfiniment. Les posts pouvaient rester bloqués au même
horaire le lendemain. Une collecte initiale était suivie d'un second fetch dans
la même fenêtre. Le scheduler radio ignorait les heures YAML et pouvait lancer
plusieurs bulletins simultanément.

La décision est maintenant isolée dans `modules/core/scheduler.py`. `main.py`
conserve les services et callbacks métier ; aucune nouvelle dépendance, file de
jobs, donnée persistante ou migration. Le générateur bulletin et son helper morts
côté news sont retirés ; la radio reste l'unique scheduler des bulletins.

## Horaires et fenêtres d'admission

Les horaires sont ceux de l'horloge locale du serveur, comme auparavant.

| Famille | Horaires conservés | Fenêtre locale d'admission |
|---|---|---|
| fetch | 24 h/24, chaque :00 et :30 | Minutes 00–01 et 30–31 |
| edition | Matin 06:00, midi 12:00, soir 19:00 | Cinq premières minutes |
| report | 23:00 | 23:00–23:04 |
| rebuild | 23:30 | 23:30–23:34 |
| posts | `config.yaml`, radio.post_hours, défaut 7/9/11/13/15/17/19/21 | Trois premières minutes de chaque heure autorisée |
| bulletin | `config/bulletins.yaml`, post_hours | Préparation :25–:26 et :55–:56, pour la prochaine demi-heure réelle |

Les bornes ci-dessus comprennent les secondes de la dernière minute. Ce sont des
fenêtres de lancement ; une tâche peut terminer après l'horaire. Le scheduler ne
transforme pas la queue Icecast en calendrier de diffusion à heure fixe.

Le fetch initial et son rebuild restent exécutés avant les autres tâches. Ils
réservent le créneau courant pour éviter le doublon de démarrage, puis l'horloge
est relue : un démarrage long ne déclenche pas des tâches avec une heure périmée.
Si la collecte initiale chevauche le créneau suivant, celui-ci peut encore être
admis après sa fin si sa fenêtre est toujours ouverte, comme historiquement.

## Attente et configuration

La boucle attend via `Event.wait` jusqu'à la prochaine minute : 0,05 à 60 secondes.
Elle ne tourne plus activement au repos ; le signal d'arrêt interrompt l'attente.
L'horloge, l'attente et les tâches sont injectables dans les tests. Aucune mesure
CPU live du moteur n'est revendiquée : les tests vérifient le passage par l'attente
à chaque tour, sans vraie collecte ni temporisation longue.

Les demandes existantes `data_dir/.reload_config` sont vérifiées à la minute.
Le dossier data_dir configuré est respecté. Si des tâches news sont actives, le
flag est conservé et le reload différé pour éviter de muter leurs services en cours.
Dès qu'elles sont toutes terminées, les modules et post_hours sont rechargés ; les
rebuilds suivants reçoivent le nouveau dict config. L'historique de créneaux est
conservé. Une erreur de reload garde le comportement historique de consommation
du flag et est journalisée ; une nouvelle demande reste nécessaire. Une erreur
partielle de reload de modules n'est pas une transaction de configuration complète.

Le calendrier radio est relu à chaque vérification. Un calendrier invalide conserve
les dernières heures valides, ou désactive les admissions si aucune n'a été chargée.
Les listes d'heures exigent des entiers de 0 à 23 ; une liste vide désactive les tâches
concernées. L'absence du fichier utilise les défauts existants de load_bulletins_config.

## Identité, threads et doublons

Une tentative est identifiée par famille et date/heure civile, par exemple
`fetch:2026-11-03T07:30`. Les heures ne se confondent plus entre deux dates, mois ou
années. Toutes les éditions appartiennent à la même famille edition ; tous les
fetchs à fetch, posts à posts, bulletins à bulletin. Une seule occurrence de chaque
famille peut être active dans un processus, indépendamment du créneau.

ThreadRunner protège sous un même verrou la vérification, la réservation et le
lancement. Les références des threads terminés sont libérées à leur sortie et
nettoyées lors des admissions ; elles ne croissent plus avec les jours. Les tâches
sont non-daemon et peuvent terminer avant l'arrêt. Les familles distinctes gardent
leur concurrence historique ; les rebuilds incorporés dans fetch/edition peuvent
toujours chevaucher un autre callback métier. Le verrou LLM reste une autre PR.

Un créneau observé avec une tâche précédente encore active est consommé et signalé
`[SKIP] ... précédente exécution active`, une seule fois. Il n'est pas rejoué dès
que la tâche finit. Un échec de tâche ou de démarrage n'est pas réessayé dans le
même créneau. C'est une politique d'admission, pas une garantie de succès métier.
L'historique contient au plus 1 024 tentatives par scheduler ; les plus anciennes
sont évincées. Une correction d'horloge reculant au-delà de cet historique peut
rejouer un ancien créneau.

Au passage à l'heure d'hiver, les deux occurrences d'une même heure civile partagent
l'identité : chaque demi-heure ou heure n'est admise qu'une fois dans ce processus.
À l'heure d'été, les heures inexistantes ne sont pas rattrapées. La cible radio est
calculée sur les timestamps réels : à 01:55 avant le saut printanier Paris, la cible
est 03:00, pas une heure fictive 02:00. Les tests utilisent explicitement Europe/Paris.
Ne pas changer de fuseau sur un processus actif ; une configuration de fuseau
applicative versionnée reste une évolution ultérieure.

## Radio : changement de comportement explicite

Auparavant range(0,24) imposait le h24. Le scheduler respecte maintenant les heures
**cibles** de bulletins.yaml : avec le fichier versionné, 07:00 à 21:30, préparation
06:55 à 21:25. Pour conserver une émission h24, configurer explicitement toutes les
heures 0 à 23 dans ce fichier lors d'une intervention autorisée. Aucun fichier de
configuration de l'installation n'est modifié par cette PR.

radio.post_hours du config.yaml pilote historiquement les **posts écrits** ; ce n'est
pas le calendrier bulletin. first_slot_hour n'ajoute pas de seconde règle : le champ
historique reste inutilisé, sans refonte du schéma. Voix, sélection des articles,
TTS, mix, FFmpeg, Icecast et contrats Android ne changent pas. Les flashs web manuels
restent gérés par leurs propres protections 01A, sans coordination distribuée ici.

## Arrêt, redémarrage et limites

Les enfants NewsEngine/Radio reçoivent un Event de multiprocessing neuf à chaque
création. Leur wrapper transforme SIGINT/SIGTERM en demande d'arrêt. Le parent
arrête les admissions des deux schedulers, attend leurs travaux actifs, puis leur
sortie. Radio ne stoppe ses deux streamers qu'après le dernier bulletin terminé.
Le traitement existant d'arrêt du serveur web reste distinct.

Le watchdog conserve ses vérifications toutes les 15 secondes et recrée un Event
neuf pour un enfant mort, en relisant la configuration. SIGUSR1 demande un restart
traité dans la boucle du superviseur, au plus tard au prochain tour ; le handler
ne modifie plus la liste des processus en plein contrôle du watchdog. Les schedulers
sont drainés avant leur remplacement. Le POST /config/restart existant reste un
rechargement news via flag, pas une nouvelle route de contrôle des processus.

Aucun timeout de join ne tue automatiquement un travail actif. Un traitement ou
un streamer bloqué peut donc retarder l'arrêt/restart ; ses propres timeouts et
les futures corrections LLM/radio restent nécessaires. SIGKILL, panne du parent,
limites du gestionnaire de services ou signaux reçus directement par les sous-processus
externes ne peuvent pas être neutralisés par ce scheduler. Les enfants gardent le
statut daemon de multiprocessing historique pour la supervision.

**L'historique n'est pas persistant.** Après crash/restart, les fenêtres ouvertes
peuvent être admises à nouveau, le fetch initial est relancé et les fenêtres fermées
ne sont pas rattrapées. Deux instances indépendantes ne sont pas coordonnées. Une
garantie durable ou multi-processus demandera les jobs persistants du sprint SQL,
avec idempotence métier et politique de reprise explicite. Aucun « exactly once »
global n'est revendiqué ici.

## Option Radio Studio — Sprint 11

Sans option, les garanties et limites en mémoire ci-dessus restent applicables.
Avec radio_studio.enabled : même boucle Radio et même autorité bulletins.yaml,
horloge explicite Paris, réservation durable par créneau avant lancement du thread.
Un job connu n'est pas régénéré après restart. Une génération active consomme le
créneau suivant avec motif ; aucune nouvelle boucle Flask. Les jobs/passes ambigus
exigent une reprise explicite d'une publication existante, pas un rejeu automatique.
Voir RADIO_STUDIO.md pour outbox, états, limites et rollback.
