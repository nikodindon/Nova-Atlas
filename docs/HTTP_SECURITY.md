# HTTP externe et SSRF — Sprint 01B

## Surfaces auditées

| Entrée | Origine des URL | Politique |
|---|---|---|
| ArticleFetcher._fetch_rss | feeds.yaml, ajout admin, sources historiques | modules.http.external.get |
| ArticleFetcher._fetch_content | Liens des items RSS, articles en attente de résumé | Même client externe |
| Parsing XML RSS | DTD et entités XML potentiellement externes | Réseau, DTD et résolution d'entités explicitement désactivés |
| Flask /config/feeds/add | URL administrateur | parse_url sans réseau à l'ajout ; DNS et socket revalidés à chaque collecte |
| Flask /stream/<mount> | Mount de requête, origine configurée localement | modules.http.icecast, politique interne distincte |
| Client LLM /v1/models et /v1/chat/completions | base_url configurée par l'administrateur | Service de confiance, inchangé, hors politique des sources |
| Edge TTS, FFmpeg/Icecast | Configuration de service | Inchangés ; aucun test live |

L'extracteur HTML ne charge ni images, CSS, scripts ni liens secondaires. Tout futur
test/découverte/import de source doit utiliser **external.get**, jamais requests,
urlopen ou un navigateur pour récupérer directement une URL non fiable. Le client
renvoie content, text, status_code, headers, url finale et raise_for_status().
Le support RSS/Atom du parseur existant n'est pas étendu dans ce sprint.

## Politique externe

Seuls `http` sur port 80 et `https` sur port 443 sont acceptés, ports explicites
équivalents admis. Pas de userinfo, chemins réseau relatifs à l'entrée, backslash,
contrôles/espaces bruts, autorité percent-encodée, zone IPv6 ou port alternatif.
IDNA et chemins Unicode sont normalisés ; path/query existants sont conservés,
les fragments non envoyés. Les adresses IPv4 abrégées, entières, hexadécimales ou
à zéros initiaux sont refusées. Les noms sans domaine et suffixes locaux sont refusés.

Une destination doit être globalement routable selon ipaddress **et** la politique
conservatrice supplémentaire. Sont notamment bloqués :

- Loopback 127/8 et ::1, non spécifiées 0/8 et ::.
- RFC1918 10/8, 172.16/12, 192.168/16, donc les réseaux Docker usuels.
- Link-local 169.254/16, fe80::/10 ; ULA fc00::/7 et ancien site-local fec0::/10.
- CGNAT/Tailscale 100.64/10, y compris metadata Alibaba 100.100.100.200.
- Métadonnées AWS/GCP 169.254.169.254, AWS ECS 169.254.170.2,
  fd00:ec2::254 et adresse spéciale Azure 168.63.129.16.
- Multicast, réservées, documentation, benchmark et plages spéciales non globales ;
  192.0.0/24 et ancien relais 192.88.99/24 explicitement refusés.
- IPv6 hors 2000::/3 et plages spéciales 2001::/23, 2002::/16,
  ancien 6bone 3ffe::/16 et documentation 3fff::/20 (explicites même sous Python 3.12) : IPv4 mappées,
  NAT64, Teredo et 6to4 sont refusées **même si leur IPv4 incorporée est publique**.

Les listes ne prétendent pas reconnaître des plages privées arbitraires d'un
opérateur qui utiliserait des IP globales ; le routage de l'hôte reste de confiance.

## DNS, connexion et TLS

Pour chaque requête et chaque redirection :

1. Valider et normaliser l'URL.
2. Résoudre A/AAAA via le résolveur système, au maximum 64 adresses.
3. Refuser **toute** la destination si une seule réponse est interdite.
4. Créer une socket AF_INET/AF_INET6 et appeler connect avec la première **IP
   numérique validée**, sans seconde résolution du nom et sans proxy.
5. Vérifier le peer de la socket ; pour HTTPS, garder le nom DNS dans SNI et dans
   la vérification du certificat par SSLContext par défaut, CA système.

Le DNS peut changer après l'étape 3 : la connexion reste fixée à l'adresse validée.
Une redirection vers le même nom provoque une nouvelle validation DNS. La connexion
n'est jamais réutilisée pour un autre nom. Les sockets et réponses sont fermées,
y compris lorsqu'un saut est refusé. Aucun monkeypatch global du résolveur en production.

Aucun certificat invalide accepté, aucune option verify=False. Le composant ne lit
ni proxies d'environnement, ni netrc, ni cookies, ni authentification implicite.
Il ne permet pas de fournir des headers arbitraires. Chaque saut reconstruit ses
headers publics et Host ; Set-Cookie/Authorization/Proxy-Authorization ne sont pas
propagés. Les paramètres explicitement présents dans une URL restent transmis à
son origine : ils ne doivent pas être journalisés ni exposés à l'administration.

## Redirections et budgets

| Ressource | Limite par défaut |
|---|---|
| URL d'entrée / Location | 4 096 caractères ; cible encodée ≤8 192 |
| Ports | HTTP 80, HTTPS 443 uniquement |
| Redirections | 5 sauts, boucles refusées, HTTPS→HTTP interdit |
| DNS | Attente 5 s ; au plus 4 résolutions système en cours par processus |
| I/O | 10 s par opération bloquante |
| Durée totale | 30 s pour le client ; 10 s dans le fetch historique, tous sauts compris |
| Corps transféré et décompressé | Chacun ≤4 Mio ; limites testables plafonnées à 8 Mio |
| En-têtes/status/framing chunked | 8 Kio par ligne, 64 Kio cumulés, plus limites http.client |
| Compression | identity ou gzip ; décompression bornée, gzip tronqué/concaténé refusé |

Un watchdog coupe la socket à l'échéance totale, y compris pendant le handshake,
les headers lents ou la lecture du corps. Sa référence de transport reste valide
quand http.client détache la socket de la connexion pour une réponse Connection: close.
Le délai DNS du caller est borné mais libc.getaddrinfo n'est pas annulable : les
workers daemon peuvent finir après le délai. Leur nombre reste borné à quatre ;
si tous sont occupés, échec fermé dns_busy, jamais création illimitée de threads.
Les budgets s'appliquent à une récupération ; les futurs tests groupés doivent
aussi borner leur concurrence et leur nombre de sources au niveau des jobs.

Un échec remonte FetchError/BlockedURL avec code générique sans URL/credential.
Les fonctions historiques gardent leurs retours []/chaîne vide et n'impriment plus
l'URL ni l'exception brute RSS. Le défaut de marquage « vu » après échec d'extraction
reste hors périmètre et requiert sa PR dédiée.

## Icecast et autres services internes

La politique publique ne doit pas casser un Icecast Docker, loopback ou Tailscale.
Le proxy web utilise donc une politique indépendante : origine hôte/port fournie par
la configuration de confiance, chemins limités exactement aux mounts configurés
(principal et Android), aucune redirection, aucun proxy/credential ambiant. Le corps
reste un flux audio continu, sans limite de taille de document ; connexions fermées
à la fin/déconnexion. Les mounts personnalisés comportant des sous-répertoires ou une extension sont
préservés lorsqu'ils correspondent exactement à la configuration, sans segments
vides, dot-segments, query ou encodage ambigu. Les redirections Icecast qui
fonctionnaient implicitement sont désormais refusées.

Les adresses des services internes sont une configuration administrateur de
confiance ; cette exception ciblée ne s'applique jamais aux sources. Aucun changement
du client LLM, du moteur radio, du TTS, de LiteLLM ou de l'application Android.

## Flux LAN et limites opérationnelles

Aucune exception réseau privé disponible dans cette PR : pas de switch global
ALLOW_PRIVATE, pas de CIDR large ajouté dans les variables d'environnement.
Pour un futur besoin LAN, prévoir un registre administrable réservé à l'admin,
avec source identifiée, schéma/hôte/port exacts et ensemble IP/CIDR minimal explicite,
audit et révocation. Les redirections devraient revalider une permission explicite
par origine ; metadata/loopback/CGNAT ne seraient jamais implicitement autorisés.
Cette extension exigera tests dédiés et pinning identique, avant activation.

Les 120 entrées RSS versionnées passent la validation statique des URL, sans requête
DNS/HTTP réelle ; cela ne garantit pas leur disponibilité. Les sources utilisant
un port alternatif, cookies, auth URL, downgrade TLS ou encodage non pris en charge
seront refusées. Une seule IP publique du RRset est essayée par saut, sans fallback
ni retry automatique : disponibilité IPv6/CDN à vérifier lors d'un test autorisé.
Pas de test réseau live ou de service démarré dans le sprint. Un pare-feu egress
reste une défense supplémentaire contre routage/NAT malveillant ou régression.
L'audit XSS/CSP et les défauts scheduler/verrou/dates/reprises/audio restent ouverts.

Références : [http.client Python 3.12](https://docs.python.org/3.12/library/http.client.html),
[ssl](https://docs.python.org/3.12/library/ssl.html),
[ipaddress](https://docs.python.org/3.12/library/ipaddress.html).
