# Validation Sprint 01B — 2026-11-03

PR #3 vérifiée avec deux CI réussies, puis fusionnée sur autorisation explicite.
Socle synchronisé : dev@3ccbb8da3cbace1aeb5dbcd70613c3226096e595. La CI sur ce
commit dev est également réussie. Branche : feature/v2-ssrf-protection.

Résultat final local : **438 réussis, 0 échec, 0 skip**, 13.02 s.
286 cas de baseline conservés ; 152 nouveaux cas HTTP/SSRF et intégration.
Les anciens mocks des fetch/proxy sont adaptés à leurs nouveaux points d'entrée.

```sh
.venv-baseline/bin/python -m pytest -q
.venv-baseline/bin/python -m compileall -q main.py modules tests
node tests/test_csrf.cjs
git diff --check
```

Tous réussis ; sortie pytest conservée dans sprint_01b.txt. Dépendances Python
inchangées, environnement Python 3.12 du Sprint 00 ; aucune nouvelle installation
système nécessaire. La CI existante lance la même suite et le contrôle JS.

## Couverture

- HTTP/HTTPS publics, IPv4/IPv6, IDNA, chemins Unicode et query de flux existants.
- Loopback, RFC1918/Docker, link-local/metadata, CGNAT/Tailscale, réservées,
  multicast, adresses non spécifiées, ULA/site-local, IPv4 mappée, NAT64/6to4/Teredo.
- Syntaxes atypiques, ports/schémas, credentials URL et scopes IPv6 refusés.
- DNS A/AAAA mixte refusé, rebinding empêché par connexion à l'IP numérique validée,
  nouvelle vérification du même nom à chaque redirection, peer de socket vérifié.
- TLS : SNI/hostname et contexte de vérification conservés, erreur de certificat fermée.
- Redirections privées, inter-domaines sans cookie/auth, boucles, 5 sauts maximum,
  downgrade HTTPS refusé ; proxies d'environnement sans effet sur le client externe.
- Socket et TLS simulés ; en complément, vrai parseur http.client sur socket fictive :
  sérialisation Host/path, Content-Length, chunked, budgets de headers et de corps.
- Timeouts DNS et I/O, budget total incluant DNS, watchdog pendant headers et socket
  détachée par http.client ; gzip borné, tronqué ou invalide, réponse trop grande.
- DTD/entités XML externes : resolver simulé prouve qu'aucune ressource n'est chargée.
- Même client pour RSS et articles ; retours historiques d'échec et fixtures conservés.
- Icecast : politique interne distincte, loopback/Docker/Tailscale/IPv6 configurés
  acceptés, mounts personnalisés exacts conservés, chemins détournés et redirects
  refusés, fermeture upstream y compris HEAD sans consommation du flux.
- Admin : auth/CSRF préservés, ajout d'une URL littérale privée/port interdit refusé.
- Les 120 entrées de feeds.yaml passent parse_url sans DNS/HTTP réel.

## Limites

Pas de test live DNS/TLS/CDN ni de radio, collecte réelle, LLM ou TTS démarré.
Le test de compatibilité des flux est statique, pas une certification de disponibilité.
Tous les tests utilisent fixtures, DNS, sockets, transport ou services simulés ; la
garde hors ligne du Sprint 00 reste active. Aucun fichier privé lu ni donnée réelle
modifiée ; aucun fichier du moteur radio/Android/LLM changé.

NOVA_ATLAS_V2_PLAN.md reste non suivi et intact. Empreinte avant/après :
8cebdaee43c604ceb3d7056e27c52052b3aba2c07d84b148532abfd8af85a1e6.

La PR SSRF attend sa revue, sans fusion automatique. Voir HTTP_SECURITY.md pour
les budgets, restrictions conservatrices, confiance dans le système/routage,
limite DNS libc et projet d'exceptions LAN strictes non encore implémentées.
