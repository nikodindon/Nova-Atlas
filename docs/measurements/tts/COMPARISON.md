# Comparaison TTS — résultats disponibles

Mesures absentes : non mesuré. Fixtures sinusoïdales exclues des performances TTS. Aucune note humaine sans écoute.

| Moteur | Modèle / voix FR | Code / poids / diffusion | RAM / VRAM | RTF CPU / GPU | Chargement | Stabilité | Qualité humaine | Déploiement / limites |
|---|---|---|---|---|---|---|---|---|
| edge | Microsoft Edge online service / fr-FR-HenriNeural / fr-FR-DeniseNeural | LGPL-3.0 client ; remote service, not distributed ; not established by client license; operator service-terms review required | non mesuré / non mesuré | non mesuré / non mesuré | non mesuré | non mesuré | non mesuré | existing dependency 7.3.1, network required ; baseline opt-in only; no service availability guarantee |
| piper | operator-pinned fr_FR-siwis-medium ONNX/config / one speaker per selected model; siwis French | GPL-3.0-or-later, piper-tts 1.8.0 ; repository MIT tag; individual card/dataset CC BY 4.0, upstream lessac provenance to review ; not automatically cleared; model-specific review and attribution required | non mesuré / non mesuré | non mesuré / non mesuré | non mesuré | non mesuré | non mesuré | optional separate environment; CPU only in this adapter ; first CPU candidate to benchmark, not a measured winner |
| kokoro | Kokoro-82M v1.0, not downloaded / ff_siwis (one listed French female voice) | Apache-2.0 ; Apache-2.0 model card; voice provenance still to review ; license obligations and voice-specific provenance review required | non mesuré / non mesuré | non mesuré / non mesuré | non mesuré | non mesuré | non mesuré | PyTorch/misaki optional heavyweight environment; no adapter in this tranche ; compact quality candidate; 4 GiB GPU fit unvalidated |
| xtts | XTTS-v2, no weights selected / French multilingual; cloning excluded | MPL-2.0 toolkit (Idiap maintained fork) ; Coqui Public Model License 1.0.0 ; model license limits model and outputs to non-commercial use; no general public-radio clearance | non mesuré / non mesuré | non mesuré / non mesuré | non mesuré | non mesuré | non mesuré | heavy optional PyTorch stack; no adapter ; excluded from initial unrestricted deployment shortlist; no GPU compatibility claim |
| f5 | F5-TTS pretrained, no French checkpoint selected / French capability/voice version not established for selected weights | MIT ; CC-BY-NC according to official project ; commercial use not cleared by this license; references and consent would also be needed | non mesuré / non mesuré | non mesuré / non mesuré | non mesuré | non mesuré | non mesuré | heavy stack; no adapter or reference voice collection ; deferred: French weights, rights and 4 GiB fit not validated |
| chatterbox | Chatterbox Multilingual V3 (0.5B), no weights selected / French listed, no voice reference selected | MIT ; official model repository MIT; exact V3 checkpoint not selected or verified ; exact weights, origin/consent of voices and watermark obligations require review | non mesuré / non mesuré | non mesuré / non mesuré | non mesuré | non mesuré | non mesuré | heavy optional stack; no adapter ; quality exploration deferred, no 4 GiB or offloading claim |

## Mesures réelles par expérience et environnement

| Moteur / modèle / voix | CPU / mode | Chargement | Premier segment après chargement | RTF premier passage | RTF suivants | RAM worker max | VRAM / GPU |
|---|---|---|---|---|---|---|---|

Les lignes ne sont pas fusionnées entre machines, modèles ou voix. Aucun passage GPU exécuté.

## Provenance des expériences

- fixture synthetic-tone 1 / tone-a : success, AMD Ryzen 5 1600 Six-Core Processor, profil A, mode cpu. Genre : synthetic_fixture.

## Recommandation conditionnelle

Piper est le premier candidat CPU à tester ; Kokoro est un candidat qualité à examiner ensuite. Aucun vainqueur mesuré sur Ryzen/i5, aucune compatibilité 4 Go VRAM ni offloading démontrés.
Conserver Edge dans la radio actuelle pour ce sprint. Sa diffusion publique et sa dépendance réseau restent à examiner. Aucun secours local qualifié tant que les essais réels et la revue des voix ne sont pas terminés.
Un audio décodable ou un RTF bas ne prouve pas que le texte a été prononcé intégralement. Pas d’ASR ni écoute automatique.
