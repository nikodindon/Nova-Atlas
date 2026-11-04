# Sécurité web — Sprint 01G

Socle : `dev@a631590`, PR #8 fusionnée sur demande explicite après ses deux CI
réussies. Aucun déploiement, collecte, accès à des secrets privés ou changement de
données de production. Le plan local reste intact. Le frontend Flask est conservé.

## Reproductions et inventaire

Les cinq reproductions exécutées **avant** correction échouaient : titre/source
RSS créant des éléments actifs, lien JavaScript, HTML brut de rapport, présence de
l'interpréteur `eval`, absence de CSP. Les tests restent des non-régressions.

| Entrée non fiable | Rendu / point sensible | Correction et vérification |
|---|---|---|
| Titres/résumés/source/catégorie JSON | `build_live_feed`, REPORT/LIVE templates, attributs data/category | Environnement Jinja avec autoescape systématique ; Unicode conservé ; payloads HTML et guillemets testés |
| Liens des articles | href dans direct, rapports, sources et breaking banner | `safe_url` HTTP(S) absolu, hostname requis, ni credentials, espaces/contrôles ni backslash ; attribut toujours échappé |
| Groupes breaking | `render_breaking_banner`, HTML construit en Python | Texte/label/lien échappés explicitement ; seuls badges et éléments du site sont du HTML de confiance |
| Rapports Markdown issus du LLM/articles | `render_report_page` → report-body | Markdown puis sanitisation HTML5 nh3, jamais `safe` sur le contenu brut |
| Éditions Markdown et titre éditorial | `render_edition_page` → titre/body | Titre échappé, corps soumis au même sanitiseur ; rendu final testé |
| Branding/configuration historique | Tous templates, inputs et titre de page | Autoescape ; `safe_view(..., escape_html=False)` garde le masquage des secrets et laisse Jinja encoder une seule fois |
| Configuration admin et erreurs | `/config/simple`, `/config/yaml`, sauvegardes | Scripts statiques et actions déclaratives connues ; validation serveur/masquage/écritures inchangés ; aucun message d'erreur inséré comme HTML |
| Catégories/URL de feeds.yaml | Éditeur RSS, JSON `/config/feeds` | Construction DOM createElement/textContent/dataset, aucun littéral JS ou handler reconstruit à partir des données ; schéma URL vérifié avant lien |
| Préférences et catégories | Page préférences + filtre public | DOM texte, événements contrôlés ; modifications toujours authentifiées et CSRF |
| API articles/abonnement/préférences | JSON public pour Android et navigateur | Contrats et chaînes brutes conservés ; application/json + nosniff ; aucun JSON arbitraire placé dans du code JS |
| Pages reçues par navigation/refresh | Ancien DOMParser → innerHTML → eval | Remplacement DOM importNode/replaceChildren, suppression scripts/handlers/éléments actifs, aucun interpréteur ; initialisation explicite des composants |
| Scripts et événements inline | Templates, éditeur RSS, config, flashs | Sept fichiers statiques connus dans `/assets/` ; handlers remplacés par data-action et addEventListener |
| URL Icecast/config/ancien localStorage | Lecteur radio web | URL de même origine `/stream/<mount>` sur HTTP et HTTPS ; ancien URL externe mémorisé ignoré ; client Icecast interne inchangé |
| Flashs et progression | flash.js et API privées | Script statique, messages textContent, endpoint audio de même origine ; contrôle radio 01F et auth/CSRF conservés |
| Exports HTML | `generate_static_site` | Même rendu sûr, copie des assets du dépôt ; en-têtes à configurer sur le serveur statique, voir ci-dessous |

Les sorties HTML volontairement de confiance (`Markup`) se limitent aux CSS/scripts
**du dépôt**, aux fragments de navigation construits avec données échappées et au
résultat du sanitiseur. Une donnée de flux/configuration ne doit jamais être
convertie directement en `Markup`. Les balises de template contenues dans une
chaîne d'article ne sont pas évaluées une seconde fois.

## Politique de HTML éditorial

`web_security.render_markdown` utilise [nh3](https://nh3.readthedocs.io/en/latest/)
avec une allowlist explicite : paragraphes, titres, listes, citations, emphase,
code, tableaux, liens, exposants/indices et listes de définitions. Les liens
n'admettent que href/title et HTTP(S) absolu ; rel noopener/noreferrer est ajouté.
Pas d'images externes, CSS, attributs d'événements, SVG/MathML, formulaires,
iframe/objet, script ni HTML personnalisé actif. Les contenus de script/style et
autres conteneurs actifs sont supprimés. Les classes et attributs arbitraires sont
retirés. Ceci conserve le Markdown éditorial attendu, pas tout HTML d'un auteur.

La politique des liens rendus n'est **pas** le client SSRF : elle autorise les liens
HTTP(S) destinés au navigateur, sans DNS ni requête serveur. Toute récupération
serveur demeure soumise au client du Sprint 01B ; aucune exception réseau ajoutée.

## JavaScript et navigation

- `web.js` : initialisation explicite du thème, lecteur, filtres, préférences,
  résumés et sources ; navigation publique et refresh. Un unique objet Audio reste
  hors du contenu remplacé, y compris précédent/suivant. La sélection multivaluée
  est réappliquée après navigation/refresh ; aucune chaîne reçue n'est du code.
- `flash.js` : parcours existant déplacé en statique, initialisé par événements.
  La topbar et son état ne sont pas détruits par la navigation publique.
- `config.js`, `simple-config.js`, `feeds.js`, `preferences.js` : initialisations
  limitées aux composants présents, données via valeurs/dataset/textContent.
- `csrf.js` : chargé avant les interactions. Les pages privées authentifiées ont
  une meta CSRF et le formulaire de logout. **Le HTML public, même authentifié,
  n'embarque ni token ni formulaire admin.** Pour une mutation depuis une page
  publique, le helper obtient `/admin/csrf` au moment de l'action, avec la session,
  puis enrichit la requête de même origine. Jamais de token vers une autre origine.

La navigation par fetch ne vise qu'une allowlist de pages publiques de même
origine. Les liens externes, ancres, téléchargements, clics modifiés et pages
privées gardent leur navigation normale. Les pages admin/préférences changent donc
encore par chargement complet ; la persistance audio concerne le parcours public,
comme auparavant, désormais aussi le direct et l'historique navigateur. Pas de
migration SPA/SvelteKit. Les interactions ne dépendent plus de scripts du HTML reçu.

## CSP et en-têtes

Politique appliquée à toutes les réponses Flask, y compris erreurs, JSON et assets :

```text
default-src 'self'; script-src 'self'; script-src-attr 'none';
style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self';
connect-src 'self'; media-src 'self'; object-src 'none'; base-uri 'self';
frame-src 'none'; frame-ancestors 'none'; worker-src 'none'; form-action 'self'
```

Aucun unsafe-eval, script inline, nonce ou hash à synchroniser. Les fichiers JS
sont servis depuis une allowlist exacte, sans fichier utilisateur. La CSP n'est
pas un remplacement de l'encodage ni du sanitiseur. La politique est testée dans
Chromium **avant livraison** : parcours normaux sans violation, attaques inline
volontaires bloquées. Les tests restent actifs en CI.

Exception ciblée : `style-src 'unsafe-inline'` conserve les feuilles et attributs
CSS historiques. Les styles non fiables sont exclus du Markdown. Les polices
Google externes et le SVG data: du select sont retirés ; polices de repli locales
et flèche native, sans nouvelle source externe. Aucun domaine CDN/média supplémentaire.
L'iframe cachée inutilisée de l'ancien lecteur disparaît. Mise en iframe refusée.

Autres en-têtes : nosniff, Referrer-Policy same-origin, X-Frame-Options DENY,
Permissions-Policy camera/microphone/geolocation/payment désactivés. Les règles de
cache privées/no-store et les cookies sécurisés du Sprint 01A sont conservés.

HSTS est **désactivé par défaut**. `ATLAS_HSTS_MAX_AGE` (0…31536000, défaut 0) ne
produit un en-tête que sur une requête HTTPS reconnue. Pas de preload ni
includeSubDomains implicite. Ne pas l'activer sur une installation HTTP locale ou
avant de garantir durablement HTTPS sur le domaine concerné.

## nginx, Tailscale et exports

Le proxy doit transmettre le Host attendu et remplacer X-Forwarded-Proto par le
schéma qu'il a réellement servi, jamais relayer aveuglément celui du client.
`ATLAS_TRUSTED_PROXIES` doit contenir uniquement les CIDR des pairs immédiats
réellement contrôlés. La logique TrustedProxy existante reste la seule autorité
sur le HTTPS annoncé. Tailscale ne remplace ni authentification ni CSRF.

Exemple de bloc **proxy**, dans le virtual host HTTPS déjà configuré par l'opérateur :

```nginx
location / {
    proxy_pass http://127.0.0.1:5055;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
}
```

Préserver les en-têtes CSP de Flask et la diffusion longue `/stream/`. Ne pas
ajouter une seconde CSP incompatible : plusieurs politiques se cumulent. Garder
les assets disponibles ; désactiver les anciens caches HTML/JS lors du déploiement.
Avec Tailscale Serve, vérifier le pair immédiat et le comportement HTTPS avant de
renseigner le CIDR de confiance ; aucune confiance générale envers le réseau CGNAT.

Un serveur nginx servant **directement** les exports doit ajouter les mêmes
en-têtes, notamment CSP/frame-ancestors, avec `always`. Il ne bénéficie pas des
hooks Flask. Régénérer intégralement les exports avec le nouveau code et copier
`site/assets/` lors d'un déploiement approuvé ; les anciens exports restent anciens
jusqu'à cette opération. Aucune régénération des données réelles n'a été faite ici.
Les fichiers exportés ouverts en file:// ne constituent pas un déploiement supporté.

## Validation et limites

Fixtures d'articles/config/Markdown hostiles : tags, guillemets, catégories/sources,
URL interdites, fermeture de script dans les données, Unicode, HTML déformé et
erreurs admin. Les API publiques conservent leurs contrats. Toute la baseline
incluant auth/CSRF, SSRF, scheduler, verrou LLM, articles et audio est exécutée.

Les cinq tests Chromium utilisent [Playwright](https://playwright.dev/python/docs/network)
local : toutes les URL sont interceptées puis remplies par le test client Flask,
sans serveur, DNS réel, navigateur distant ou service RSS/LLM/TTS/Icecast. Les
médias sont simulés, pas de mesure acoustique ou de streaming réel. Un proxy fermé
et la désactivation des résolutions réseau empêchent les sorties non interceptées.
Seuls les processus locaux Playwright/Chromium sont autorisés par leur fixture.
Le formulaire de login est soumis sans suivre le redirect HTTP dans le pont simulé,
puis la page cible est chargée explicitement ; les cookies et CSRF réels sont testés.

Installation développeur (téléchargement initial nécessaire, tests ensuite offline) :

```sh
.venv-baseline/bin/python -m pip install -r requirements-dev.lock
.venv-baseline/bin/python -m playwright install --with-deps chromium --only-shell
.venv-baseline/bin/python -m pytest -q
node tests/test_csrf.cjs
for script in modules/web/assets/*.js; do node --check "$script"; done
```

nh3 est une dépendance de production épinglée ; Playwright et ses dépendances sont
uniquement dans le lock de développement. Pas de test navigateur ignoré par défaut :
installer le binaire et ses bibliothèques. La CI le fait pour les deux workflows
push/PR. Le transport de test ne vérifie pas nginx, Tailscale ou les codecs audio.

Limites : une compromission des fichiers JS de confiance ou du serveur/compte local
reste hors garantie. Pas de Trusted Types ni CSP stricte pour les styles à ce stade.
Les liens HTTP(S) externes restent une sortie de navigation potentiellement hostile ;
pas d'affirmation de sûreté éditoriale du site destination. Les configurations et
exports externes doivent être revus au déploiement. Aucun changement Android,
collecteur, client LLM ou pipeline radio. Pas de promesse d'absence universelle de
XSS hors des surfaces inventoriées et testées ; conserver les tests et revoir toute
nouvelle insertion HTML, ressource ou route. L'incident de clé historique reste une
action de révocation du propriétaire, sans secret affiché ni révocation revendiquée.
