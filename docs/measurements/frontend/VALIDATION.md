# Validation Sprint 12

- Prérequis : PR19 fusionnée dans dev (8d5e2b5), huit contrôles précédents réussis.
- Suite Python complète : **1565 passed**, aucun échec/skip, 68,21 secondes localement.
  Inclut deux tests reproduisant puis verrouillant le parsing tmpfs corrigé.
  Inclut les scénarios Chromium historiques et les tests de protections antérieures.
- Frontend : **33 tests unitaires/composants** ; **10 scénarios Chromium** desktop/mobile.
- TypeScript/Svelte : zéro erreur et zéro avertissement ; ESLint réussi.
- Build de production, budget client, compilation Python, syntaxe JS historique,
  helper CSRF, configuration Compose et diff Git vérifiés.
- npm : quatre avis faibles transitifs deepmerge, zéro avis modéré/high/critical après
  override minimatch ; aucun downgrade automatique dangereux.
- Smokes Docker/RSS/images/événements/TTS lab/Radio Studio/LiteLLM/PostgreSQL conservés
  en CI. Nouveau smoke Node/nginx/Flask réel à données synthétiques ; statut final à
  consulter dans les checks GitHub de la PR.

Reproductions conservées : API documents/archives manquantes, refus des paramètres,
symlinks/données corrompues, exposition de champs privés évitée par projection,
bootstrap session/CSRF, texte et liens hostiles inertes, attributs img SSR incompatibles
avec la CSP, style d'annonceur Kit hashé, reconnexions bornées et identité audio
préservée en navigation. Aucun service producteur démarré localement.

Les tests audio navigateur simulent HTMLMediaElement ; aucun résultat ne constitue
une mesure de qualité ou disponibilité Icecast. Administration native limitée à la
consultation, mutations dans Flask. Pas de revue manuelle complète avec lecteur d'écran.

Le premier smoke frontend CI a détecté une liste YAML tmpfs incorrectement séparée
par les virgules. Deux reproductions échouaient avant citation de la chaîne complète ;
26 tests backend/frontend ciblés passent après correction. La CI du nouveau commit
doit confirmer le démarrage réel. La déconnexion exige aussi une réponse confirmée
de Flask et expose sinon un message explicite, sans annoncer une réussite.

Le smoke a ensuite révélé les caches FastCGI/uWSGI/SCGI par défaut de nginx,
incompatibles avec le rootfs read-only. Tous sont déplacés dans /tmp ; nginx -t
réussit dans un conteneur non-root read-only sans réseau ni volume après reproduction
de l’erreur, et les neuf tests du contrat de déploiement passent. Les logs CI
sont désormais conservés et filtrés avant destruction des seules fixtures du smoke.
