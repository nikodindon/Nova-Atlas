# Mesures Sprint 12

Build initial vérifié localement (Node 22.22.2), toutes routes : 58 502 octets JS
gzip, 3 129 octets CSS gzip. Refaire `cd frontend && node tests/budget.mjs` après build.
Le budget CI est 100 KiB JS / 15 KiB CSS gzip, pas un budget par page.

`synthetic-browser.json` : une visite froide Chromium desktop 1440×1000, faux Flask
local, JPEG de géométrie synthétique ; 319 ms jusqu'au lecteur, document 17 634 octets,
19 ressources observées, DOMContentLoaded 214 ms. Les octets transférés dépendent de
la compression du serveur de test ; pas une estimation du réseau de production.
Commande : démarrer `node tests/start.mjs` dans un terminal isolé puis
`node tests/measure.mjs`, arrêter le serveur de test ensuite. Aucun appel externe.
Ces mesures d'un seul passage ne constituent pas une distribution ni une garantie.
Lighthouse, mémoire/CPU conteneur et qualité réseau Icecast : **non mesurés**.
Les mesures brutes précèdent les dernières retouches de présentation (volume mobile
et formatage) ; le budget est recalculé en CI, ne pas présenter ces octets comme
l'empreinte immuable de toute révision future.
