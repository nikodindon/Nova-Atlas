# Sprint 16 — observation de l'instance et découpage

Inspection autorisée en lecture seule le 10 novembre 2026, matin Europe/Paris.
Aucun conteneur arrêté/reconstruit, aucun checkout déployé changé, aucune génération
LLM/TTS déclenchée, aucune configuration persistante modifiée. Les compteurs sont
une photographie, pas une mesure continue de disponibilité.

## Références et périmètre

- Checkout opérateur : `d2b92de8f759c924d69be2b884cfe33089ff33b7`, branche
  `feature/v2-field-qualification`, sans changement suivi observé.
- `dev` distant : `0e8752622bbbe16cf9b3b187bb7d69a9b1e4533b` ; PR #22 toujours
  ouverte avec contrôles réussis. Aucune fusion. La PR historique #1 reste ouverte
  vers main et ne constitue pas une autorisation de réutiliser des credentials.
- Lot 16A créé dans un worktree indépendant, depuis dev, sans dépendance à #22.
- Installation : projet Compose `atlas-lab`, configuration opérationnelle dans
  `~/atlas-lab/state/config/`, et non les exemples Git. Six conteneurs healthy au
  moment de l'observation, UID applicatif commun, rootfs en lecture seule,
  stockage partagé sous state ; ports Web publiés sur loopback. Le healthcheck
  ne prouve ni une collecte réussie ni une réception radio par un auditeur.
- Les tags d'image sont réutilisables ; leur nom ne prouve pas le commit embarqué.
  L'inspection a relevé les identités d'image, sans assimiler le checkout à une
  preuve de provenance de chaque binaire. Aucun environnement/secrets imprimé.

## Constats vérifiés et causes

| Observation | Composant / conséquence | Lot |
|---|---|---|
| `post_hours` = 7…21, `first_slot_hour: 7`, fenêtre 30 min et minimum 3 articles | Configuration active : pas de calendrier nocturne ; 8 h 30 sauté avec deux articles | 16B |
| Introduction 19, transitions 26, conclusions 20 présentes | Le prompt demande des variations, donc aucune garantie de texte exact | 16B |
| Cible 1500 ±250 mots, `length_policy: warn` | Scripts de 716, 823 et 660 mots ce matin admis après validation technique ; ne pas rétablir strict | 16B |
| Génération à 7 h 55 pour 8 h ; lecture signalée à 7 h 57 min 51 | Les heures éditoriales et de lecture sont distinctes ; aucune attente jusqu'au créneau observée | 16B, décision de programmation explicite |
| 10 `TimeoutError` News, trois réponses vides Radio dans les logs bornés sur 24 h | Le backend HTTP ne journalise ni usage ni finish_reason ; cause du vide non démontrée | 16A |
| Log « probable cold-start » et retry identique cinq secondes après un vide | Hypothèse présentée comme diagnostic sans preuve ; deux appels peuvent doubler l'occupation du verrou | 16A |
| Deux refus `invalid_or_empty_voice_segment` | Validation bloquante existante à conserver ; causes précises du script non observables sans instrumentation complémentaire | 16B |
| 287 états d'articles : 162 succeeded, 53 failed, 6 retryable, 66 discovered | Reprise persistante présente ; ne pas convertir tous les failed en timeouts sans examiner leurs causes | Suivi 16A/16D |
| 16 états de flux : 14 success, 2 not_modified | Cache conditionnel utilisé ; ce compteur historique ne prouve pas leur disponibilité instantanée | Préserver |
| `media.mode: approved_only`, 128 candidats `rights_unknown` | Refus de publication attendu ; pas un défaut de chargement navigateur | 16C |
| Articles avec `pub_date` et `timestamp` | Deux sémantiques temporelles à exposer, sans attribuer un fuseau certain aux anciennes valeurs ambiguës | 16D |

Les attentes de verrou sont plausibles et le même répertoire data est partagé.
Les journaux comptent 343 acquisitions/libérations News et 16 Radio ; ils ne
mesurent pas la durée d'attente. Aucune preuve de violation de l'exclusivité OS.
L'observation ne permet pas d'attribuer les erreurs au raisonnement Ornith, à sa
quantification, à la VRAM ou à `--reasoning off`. Aucun paramètre serveur changé.

## Audio et interfaces

Les fichiers principaux sont consommés après diffusion ; le cache Android conserve
les MP3 et reçus. Le fichier de six secondes restant à la racine audio_queue est
une ancienne fixture synthétique : il ne doit pas être pris pour un journal réel.
Le dernier bulletin Android observé possède une empreinte conforme au reçu et a
été intégralement décodé par FFmpeg (`exit 0`, aucune erreur). Un autre bulletin
réel inspecté dure 313,68 secondes. Pas d'écoute humaine, de test client Icecast
prolongé, ni de garantie que les auditeurs ont entendu le programme.

V1 locale `:15055` et V2 locale `:15173` répondent HTTP 200 avec CSP ; leurs API
`/api/public/site`, `/api/public/articles` et `/api/articles` répondent 200.
`limit=2` n'est pas un paramètre accepté par l'API publique ; son refus ne constitue
pas une panne. Les requêtes vers le domaine public reçoivent **403 Cloudflare**
depuis l'environnement d'audit. Comparaison visuelle publique **non effectuée**,
aucun contournement de la protection d'entrée tenté.

## Ordre de livraison

16A : réponses LLM, budgets et attente observable. 16B dépend de 16A pour les
budgets radio : attendre sa fusion opérateur, repartir du dev actualisé. 16C :
pipeline automatique borné ; droits inconnus privés pour l'administration
qualifiée, ou publication limitée à des sources dont l'autorisation est établie.
Ne pas transformer `auto_test` en déclaration juridique. 16D : horodatages et
régressions d'affichage prouvées. Chaque lot conserve une PR distincte ; aucune
modification de l'installation avant accord de déploiement.
