# Intégrité et reprise des articles — Sprint 01E

Socle : `dev@99bc9e4`, PR #6 fusionnée sur demande explicite, contrôles réussis.
Aucune donnée réelle n'a été lue, migrée ou modifiée pour cette livraison.
Le moteur et ses sorties JSON restent en place ; aucun service supplémentaire.

## Défauts reproduits avant correction

Sept tests échouaient sur le socle : trois frontières calendaires (octobre/novembre,
décembre/janvier, février bissextile), extraction vide marquée vue, résumé vide
publié, fichier corrompu écrasable et entrées expirées jamais purgées.
Les tests couvrent ensuite aussi février non bissextile, les bornes de rétention,
les reprises, interruptions, contrats publics et protections des sprints précédents.

## Dates et déduplication

`seen_hashes.json` conserve son format historique `{hash: "YYYYMMDD"}` ; les
anciennes dates entières sont également lisibles. La fenêtre comprend **sept dates
civiles locales : J−6 à J inclus**, soit `0 <= (aujourd'hui - date).days < 7`.
Les anciens huit jours implicites (`<= 7`) deviennent donc sept jours explicites.
Les calculs utilisent des dates calendaires, y compris aux changements de mois,
d'année et années bissextiles. `ArticleFetcher(config, clock=...)` permet une
horloge simulée ; par défaut l'horloge locale existante est conservée.

Une date future ou invalide ne prouve pas un traitement réussi. Avant retrait de
ces entrées du suivi actif, une sauvegarde intégrale est créée. Les dates valides
expirées sont purgées normalement. **Aucun article historique n'est supprimé par
cette rétention.** Un JSON illisible ou de structure inattendue est sauvegardé et
interrompt le cycle sans remplacement par un ensemble vide.

Les articles valides des sept fichiers quotidiens récents font foi pour la
déduplication, ainsi que les succès récents du journal. Un ancien hash « vu » sans
article valide n'interdit plus une reprise : l'ancien moteur pouvait marquer un
échec comme traité. L'identifiant MD5 de l'URL après `strip()` reste inchangé ;
ni canonicalisation des URL ni regroupement d'événements introduits. Plusieurs
titres/catégories pour une même URL partagent un traitement (première découverte).
Une entrée historique valide sans champ `hash` est reconnue par son URL.

Le jour de publication est figé au début du cycle. La reprise d'une publication
préparée avant minuit utilise son fichier quotidien d'origine. La migration globale
des timestamps en UTC et la séparation published/fetched/processed restent à faire.

## Journal et états

Nouveau fichier privé : `paths.data_dir/article_processing.json` (ignoré par Git).
Schéma version 1 :

```json
{
  "version": 1,
  "records": {
    "md5_de_url": {
      "status": "retryable",
      "attempts": 1,
      "item": {"title": "Exemple", "link": "https://news.example/article", "pub_date": ""},
      "category": "tech",
      "discovered_at": 1793793600,
      "updated_at": 1793793600,
      "next_attempt_at": 1793795400,
      "reason": "TimeoutError"
    }
  }
}
```

| État | Signification / prochaine action |
|---|---|
| `discovered` | Découverte sauvegardée, éventuellement différée par le quota de catégorie |
| `processing` | Tentative comptabilisée avant HTTP/LLM ; une interruption consomme cette tentative |
| `retryable` | Échec temporaire, nouvelle tentative au premier cycle après `next_attempt_at` |
| `succeeded` | Article valide écrit atomiquement ; aucune nouvelle génération |
| `failed` | Erreur permanente, budget épuisé ou rétention dépassée ; revue manuelle |
| `ignored` | Destination refusée par la protection SSRF ; aucune tentative automatique supplémentaire |

Avant publication, `processing` reçoit `prepared` (article complet avec le schéma
public existant) et `target_day` (`YYYYMMDD`). Ce checkpoint est écrit **avant** le
fichier d'articles. Si le processus s'arrête ensuite, la publication est reprise
sans HTTP ni LLM. Si le fichier contient déjà l'article, le journal est réconcilié
sans doublon. Les champs publics restent : hash, timestamp, category, title,
title_original, link, source, pub_date, summary. Un titre, lien et résumé non vides
sont nécessaires ; un marqueur `[Timeout]` ou `no_content` n'est pas un succès.
Un résumé supérieur à 16 000 caractères est refusé plutôt que tronqué.

Un article n'est ajouté à `seen` qu'après écriture réussie. Les erreurs d'un
article n'empêchent pas le suivant. Un échec de stockage arrête cependant le cycle
pour préserver l'intégrité. Les publications déjà confirmées et les publications
réconciliées sont comptabilisées dans le résultat de `run()` pour demander la
régénération du site au scheduler existant ; aucun changement du scheduler.

## Politique de reprise et configuration

Options facultatives dans la configuration locale existante (aucun secret) :

```yaml
fetch:
  max_attempts: 3
  retry_base_seconds: 1800
  retry_max_seconds: 21600
  retry_summaries: false
```

- Au plus trois tentatives par défaut, HTTP/extraction/LLM ensemble ; une tentative
  interrompue compte aussi. `max_attempts` est borné entre 1 et 20.
- Délai : `min(retry_max_seconds, retry_base_seconds * 2**(tentative-1))`.
  Aucun `sleep` de backoff : les cycles suivants reprennent les articles dus,
  même s'ils ne figurent plus dans le flux. Base bornée 1–86 400 secondes ; plafond
  au moins égal à la base, au plus 604 800 secondes. Rechargement à chaud conservé.
- `408`, `425`, `429`, `5xx`, timeouts, extraction vide et échec LLM sont temporaires.
  Les autres `4xx`, notamment `404/410`, sont définitifs ; un refus SSRF est ignoré.
- `Retry-After` de `429/503` accepte des secondes ou une date HTTP. Le délai serveur
  prime sur le backoff, même au-delà de son plafond. Un embargo supérieur à sept
  jours exige une revue manuelle (`failed`) ; aucune reprise anticipée. Un en-tête
  invalide retombe sur le backoff. Le client sécurisé expose `HTTPStatusError`,
  sous-classe compatible de `FetchError`, avec statut et en-tête borné uniquement.
- `retry_summaries` conserve le rôle d'option de réparation des anciens articles
  incomplets **du jour**. Les nouvelles reprises fonctionnent indépendamment de
  cette option. Une réparation conserve les champs historiques supplémentaires,
  remplace l'entrée en place et sauvegarde l'original ; `no_content` est retiré.
- Les erreurs de chargement RSS restent retentées au cycle de collecte suivant ;
  pas de nouveau parser RSS/Atom, de scheduler de santé des flux ni de Source Manager.

Les succès quittent le journal après sept jours écoulés. Les entrées en attente
sans checkpoint expirent trente jours après découverte et deviennent terminales.
Les échecs terminaux ne sont pas oubliés automatiquement : cela réinitialiserait
le budget et recréerait des retries infinis. Le journal est plafonné à 10 000
entrées ; au-delà, les découvertes supplémentaires sont différées avec un warning,
les reprises déjà connues continuent. Les champs de découverte sont bornés (URL et
titre 4 096 caractères, date RSS 1 024, catégorie 200). Les JSON sont bornés à 64 Mio.
Une revue/archivage manuel du suivi terminal est nécessaire à saturation ; aucune
purge automatique des articles ni des sauvegardes. Surveiller l'espace disque.

## Écritures, concurrence et sauvegardes

`article_state.py` écrit un temporaire unique dans le même répertoire, flush/fsync,
puis `os.replace` et fsync du répertoire. Un échec ne déclenche jamais une copie
non atomique sur l'original. Les temporaires normaux sont nettoyés ; une mort
brutale peut laisser un temporaire caché, jamais utilisé comme état officiel.
Les nouveaux fichiers sont privés (0600) et doivent rester lisibles par le même
utilisateur de service que News/Web. Prévoir les permissions partagées au déploiement
si ces processus utilisent des identités différentes.

Un `flock` non bloquant sur `paths.data_dir/article-integrity.lock` sérialise les
cycles et le nettoyage manuel. Ne jamais supprimer ce verrou pendant utilisation.
Tous les collecteurs partageant les articles doivent partager aussi `data_dir`,
sur un système de fichiers Linux local compatible flock/rename/fsync. Un changement
des chemins de stockage exige des collecteurs arrêtés, plutôt qu'un rechargement
pendant un cycle ; les options de retry et quotas restent rechargeables. Les lecteurs
publics bénéficient des remplacements atomiques. Ce verrou est indépendant du
verrou LLM ; aucune modification du protocole des backends locaux ni du HTTP SSRF.

Les sauvegardes `.pre-01e.<empreinte>.bak` sont créées atomiquement avant réparation,
nettoyage explicite, reprise sur un ancien jour ou retrait de dates anormales. Un
JSON corrompu reste intact et bloque le cycle, avec sauvegarde pour intervention.
Aucune migration destructive du format historique ; le seul nouveau format est
le journal versionné. Les sauvegardes peuvent contenir les URL et contenus sources :
les garder privées et hors Git, ne pas les joindre à des tickets publics.

## Reprise manuelle et rollback

Arrêter les collecteurs par le mécanisme habituel et sauvegarder **ensemble** le
journal, `seen_hashes.json`, les fichiers quotidiens concernés et les `.bak` avant
intervention. Ne pas éditer un JSON pendant un cycle. Pour réarmer un échec terminal,
examiner la cause puis retirer uniquement son enregistrement du journal (ou remettre
`discovered`, `attempts: 0`, `next_attempt_at: 0`) dans une copie validée. Ceci est
une réinitialisation explicite du budget ; ne jamais réarmer en masse les refus SSRF.

Pour rollback du code, conserver le journal à part : l'ancienne version l'ignore,
et les anciens formats articles/seen restent lisibles. Restaurer une sauvegarde
uniquement après comparaison et accord de l'opérateur, sans écraser les articles
publiés depuis. Un rollback réintroduit les défauts d'origine de rétention/retry.
Aucune de ces opérations n'a été exécutée sur les données de l'installation.

## Limites

La fenêtre de déduplication est volontairement de sept jours, pas éternelle. Une
ancienne URL hors fenêtre peut être publiée à nouveau. Un article définitivement
perdu avant ce sprint ne peut être retrouvé à partir du hash seul : son URL doit
réapparaître dans un flux ou être fournie par une réparation explicite.

Il n'y a pas de transaction entre plusieurs fichiers ni avec la génération du site.
Le checkpoint permet la réconciliation, mais une mort **avant** sa persistance peut
répéter une génération (le cache LLM existant peut l'éviter, sans garantie après
expiration). Une mort après le retour de `run()` et avant la reconstruction du site
n'a pas d'accusé de réception durable ; l'API JSON reste disponible, un prochain
build du site est nécessaire. Les garanties distribuées/transactions SQL restent
au sprint PostgreSQL. Aucun mécanisme ne corrige un stockage défaillant ou une
édition externe concurrente non coopérative des JSON.
