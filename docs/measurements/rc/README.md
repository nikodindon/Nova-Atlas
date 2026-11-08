# Mesures RC — 8 novembre 2026

Données entièrement synthétiques, processus Python CPU-only sur l'hôte décrit dans
host-cpu-only.json (Ryzen 5 1600, environ 32 Go RAM, Linux x86_64). Pas de GPU utilisé,
pas d'IP/hostname/identité machine collectés. La qualification complète des profils
A et B n'est pas effectuée ; B n'a pas été accessible. La RAM maximale est le
ru_maxrss du processus Python, pas celle de toute la stack ni un budget conteneur.

| Mesure | Avant : droits relus par carte | Après : droits par page + cache texte SHA |
|---|---:|---:|
| Articles du jour / sources / candidats images | 1200 / 240 / 100 | 1200 / 240 / 100 |
| Articles historiques totaux | 1200 | 2200 |
| Événements dérivés d'un sous-corpus borné | 240 | 240 |
| Moyenne de 10 pages publiques de 50 articles | 2,795 s | 0,083 s |
| Maximum, incluant page froide | 2,858 s | 0,454 s |
| 12 lectures / 4 threads test client Flask | 44,36 s | 1,11 s |
| Pic processus | 86 892 Kio | 78 692 Kio |
| Snapshot / restauration | 0,210 / 0,152 s | 0,244 / 0,172 s |

Mesure unique par état, non une distribution statistique. Le corpus actif des pages
est identique ; cinq journées historiques ont été ajoutées au second jeu. Ne pas
comparer les temps globaux comme un benchmark strictement identique. Aucun serveur
HTTP public, Node, réseau, téléchargement de flux ou génération LLM/TTS dans ce test.
Les 240 parsings RSS prennent environ 0,20 s ; leur coût réseau reste non mesuré.

La reconstruction refuse explicitement >500 articles : test du plafond, pas une
preuve de capacité événementielle sur 2200 articles. Le sous-corpus de 240 entrées
est séparé uniquement dans les fixtures, sans changer la limite de production.
Le MP3 du round-trip est une fixture d'empreinte/reçu, jamais mise en file ni jouée.
Le décodage MP3 réel demeure couvert par le smoke Radio Studio/FFmpeg distinct.
Les droits d'image sont des preuves synthétiques sur une géométrie créée pour le
smoke, pas une validation juridique de ressources Commons réelles.

Reproduction : `python -m deploy.rc_validation --output rapport-neuf.json` dans le
venv de développement ; fichiers temporaires supprimés à la fin. Sortie refusée si
le rapport existe. Le script reste borné à 2000 articles du jour / 300 sources et
4 threads ; aucune donnée de l'installation n'est importée. CI conserve son propre
rapport en artifact, les temps d'un runner ne sont pas ceux d'une machine Mint.

license-metadata.json est un inventaire des métadonnées des locks/distributions,
pas une validation des obligations de redistribution. npm-audit.json conserve les
quatre avis faibles sans contenu utilisateur ; les propositions de downgrade Kit
anciennes de npm ne sont pas appliquées. Aucun audit CVE complet des couches OS
ni score Lighthouse n'est revendiqué.

La clé `snapshot_entries` des rapports locaux désigne fichiers **et** répertoires
(ancien libellé snapshot_files clarifié sans changer la mesure). La commande/CI
courante publie séparément le nombre de fichiers ordinaires. Les rapports locaux
ne contiennent ni valeur de secret ni chemin privé de l'installation.
