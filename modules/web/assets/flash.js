(function(){
"use strict";

// ============ FLASH FEATURE ============
let flashPollInterval = null;
let flashCurrentJobId = null;
let flashWasRadioPlaying = false;  // mémorise si la radio live jouait avant le flash
let flashStartTime = null;          // timestamp du début du flash (pour ETA)
let flashLastProgress = 0;          // dernier progress vu (pour estimer vitesse)
let flashLastPollTime = null;       // timestamp du dernier poll

// Estimation du temps restant (modèle par phases, pas d'extrapolation linéaire).
// Chaque phase a un temps typique connu. L'ETA est calculée comme la somme
// des temps restants typiques des phases en cours + à venir.
const FLASH_PHASE_ESTIMATES = {
  // progress: temps total typique écoulé depuis le début (en secondes)
  0:   0,     // début
  10:  2,     // collecte articles: 2s
  20:  5,     // début LLM: 5s
  30:  30,    // LLM en cours: 30s typiques (varie 10s-2min)
  40:  60,    // LLM long: 60s
  48:  90,    // fin LLM: 90s
  50:  95,    // juste après LLM
  60:  105,   // début TTS: 105s
  70:  120,   // TTS en cours
  80:  140,   // TTS presque fini
  90:  155,   // mix audio: 155s
  95:  160,   // mix presque fini
  100: 165,   // terminé
};

function _flashEta() {
  if (!flashStartTime || flashLastProgress >= 100) return null;
  const now = Date.now();
  const elapsed = (now - flashStartTime) / 1000;
  if (elapsed < 1) return null;

  // Interpolation linéaire entre 2 phases consécutives pour un ETA
  // fluide (pas de saut entre 2 paliers).
  const sortedPcts = Object.keys(FLASH_PHASE_ESTIMATES).map(Number).sort((a,b)=>a-b);
  let lowerPct = 0, upperPct = 100;
  let lowerT = 0, upperT = FLASH_PHASE_ESTIMATES[100];
  for (let i = 0; i < sortedPcts.length; i++) {
    if (sortedPcts[i] <= flashLastProgress) {
      lowerPct = sortedPcts[i];
      lowerT = FLASH_PHASE_ESTIMATES[lowerPct];
    } else {
      upperPct = sortedPcts[i];
      upperT = FLASH_PHASE_ESTIMATES[upperPct];
      break;
    }
  }
  // Interpolation linéaire entre lowerPct et upperPct
  let typicalTotal;
  if (upperPct === lowerPct) {
    typicalTotal = lowerT;
  } else {
    const t = (flashLastProgress - lowerPct) / (upperPct - lowerPct);
    typicalTotal = lowerT + t * (upperT - lowerT);
  }
  const eta = typicalTotal - elapsed;
  return eta > 0 ? Math.round(eta) : 0;
}

function flashToggle(e) {
  e.stopPropagation();
  const dd = document.getElementById('flash-dropdown');
  const prog = document.getElementById('flash-progress');
  // Si progress visible, ne pas ouvrir le dropdown
  if (prog.classList.contains('open')) return;
  dd.classList.toggle('open');
}

// Fermer le dropdown si on clique ailleurs
document.addEventListener('click', function(e) {
  const wrap = document.getElementById('flash-wrap');
  if (wrap && !wrap.contains(e.target)) {
    const dd = document.getElementById('flash-dropdown');
    if (dd) dd.classList.remove('open');
  }
});

async function flashStart(category) {
  const dd = document.getElementById('flash-dropdown');
  const prog = document.getElementById('flash-progress');
  const btn = document.getElementById('flash-btn');
  const fill = document.getElementById('flash-progress-fill');
  const label = document.getElementById('flash-progress-label');
  const msg = document.getElementById('flash-progress-msg');

  dd.classList.remove('open');
  prog.classList.add('open');
  btn.disabled = true;
  fill.style.width = '5%';
  label.textContent = `Flash ${category}...`;
  msg.textContent = 'Envoi de la demande...';
  // Init timestamps pour l'ETA
  flashStartTime = Date.now();
  flashLastProgress = 5;
  flashLastPollTime = Date.now();

  // Mémorise l'état de la radio live (jouait-elle ?) pour la reprendre
  // à la fin du flash
  if (window._novaAudio) {
    flashWasRadioPlaying = !window._novaAudio.paused;
    if (flashWasRadioPlaying) {
      window._novaAudio.pause();
      msg.textContent = 'Envoi de la demande... (radio live mise en pause)';
    }
  }

  try {
    const r = await fetch('/api/flash', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({category: category})
    });
    const data = await r.json();
    if (data.status !== 'ok') {
      msg.textContent = 'Erreur: ' + (data.msg || 'inconnue');
      btn.disabled = false;
      // Reprend la radio si on l'avait mise en pause
      if (flashWasRadioPlaying && window._novaAudio) {
        window._novaAudio.play().catch(() => {});
        flashWasRadioPlaying = false;
      }
      return;
    }
    flashCurrentJobId = data.job_id;
    msg.textContent = 'Job ' + data.job_id;
    // Polling toutes les 1.5s
    flashPollInterval = setInterval(() => flashPoll(), 1500);
  } catch (e) {
    msg.textContent = 'Erreur réseau: ' + e.message;
    btn.disabled = false;
    // Reprend la radio si on l'avait mise en pause
    if (flashWasRadioPlaying && window._novaAudio) {
      window._novaAudio.play().catch(() => {});
      flashWasRadioPlaying = false;
    }
  }
}

async function flashPoll() {
  if (!flashCurrentJobId) return;
  const fill = document.getElementById('flash-progress-fill');
  const label = document.getElementById('flash-progress-label');
  const msg = document.getElementById('flash-progress-msg');
  const btn = document.getElementById('flash-btn');
  const prog = document.getElementById('flash-progress');

  try {
    const r = await fetch('/api/flash/status/' + flashCurrentJobId);
    const data = await r.json();
    if (data.status === 'error') {
      clearInterval(flashPollInterval);
      fill.style.width = '100%';
      fill.style.background = '#ef4444';
      label.textContent = '❌ Erreur';
      msg.textContent = data.error || 'Erreur inconnue';
      // Reset timestamps
      flashStartTime = null;
      setTimeout(() => {
        prog.classList.remove('open');
        btn.disabled = false;
        fill.style.background = '';
      }, 3000);
      return;
    }
    if (data.status === 'done') {
      clearInterval(flashPollInterval);
      fill.style.width = '100%';
      label.textContent = '✅ Flash prêt !';
      msg.textContent = `${data.articles_count} articles, lecture...`;
      // Reset timestamps
      flashStartTime = null;
      flashLastProgress = 100;
      // Jouer le mp3
      const audio = new Audio('/audio/flash_' + flashCurrentJobId + '.mp3');
      audio.play().catch(e => {
        msg.textContent = 'Erreur lecture: ' + e.message;
      });
      audio.onended = () => {
        msg.textContent = 'Terminé';
        // Reprend la radio live si elle était en cours avant le flash
        if (flashWasRadioPlaying && window._novaAudio) {
          window._novaAudio.play().catch(() => {});
          flashWasRadioPlaying = false;
        }
        setTimeout(() => {
          prog.classList.remove('open');
          btn.disabled = false;
          fill.style.background = '';
        }, 1500);
      };
      return;
    }
    // En cours : animation continue via CSS transition (1.4s)
    // L'ETA est calculée par extrapolation linéaire de la vitesse observée
    fill.style.width = Math.max(5, data.progress || 5) + '%';
    flashLastProgress = data.progress || 5;
    flashLastPollTime = Date.now();
    const step = data.progress < 30 ? 'Collecte des articles' :
                 data.progress < 50 ? 'Génération du script (LLM)' :
                 data.progress < 90 ? 'Synthèse vocale (TTS)' :
                 'Mixage audio';
    const eta = _flashEta();
    const etaStr = eta !== null ? ` (~${eta}s restantes)` : '';
    msg.textContent = step + ' (' + data.progress + '%)' + etaStr;
  } catch (e) {
    msg.textContent = 'Erreur polling: ' + e.message;
  }
}

document.addEventListener('click', function(event){
 const button=event.target.closest('[data-action]'); if(!button)return;
 if(button.dataset.action==='flash-toggle')flashToggle(event);
 if(button.dataset.action==='flash-start')flashStart(button.dataset.cat);
});
})();
