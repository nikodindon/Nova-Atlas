(function(){
"use strict";
document.addEventListener("DOMContentLoaded",()=>{
if(!document.getElementById("llm-provider"))return;

        // Affiche/masque le champ base_url selon le provider
        document.getElementById('llm-provider').addEventListener('change', function() {
          document.getElementById('row-llm-baseurl').style.display =
            this.value !== 'ollama' ? '' : 'none';
        });


// ── Navigation sidebar ────────────────────────────────────────────────────────
function showSection(id, link){
  document.querySelectorAll('.cfg-section').forEach(s=>s.classList.remove('active'));
  document.querySelectorAll('.cfg-nav-btn').forEach(a=>a.classList.remove('active'));
  document.getElementById('sec-'+id).classList.add('active');
  link.classList.add('active');
  return false;
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(indId){
  var t = document.getElementById('save-toast');
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2500);
  if(indId){
    var ind = document.getElementById(indId);
    if(ind){ ind.classList.add('show'); setTimeout(()=>ind.classList.remove('show'),3000); }
  }
}

// ── Sauvegardes par section ───────────────────────────────────────────────────
function post(data, ind){
  return fetch('/config/yaml/save',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(data)
  }).then(r=>r.json()).then(d=>{ if(d.status==='ok') showToast(ind); });
}

function saveService(){
  Promise.all([
    post({section:'service',key:'name',    value:document.getElementById('svc-name').value},'ind-service'),
    post({section:'service',key:'tagline', value:document.getElementById('svc-tagline').value},'ind-service'),
    post({section:'service',key:'default_language',value:document.getElementById('svc-lang').value},'ind-service'),
  ]);
}

function saveOllama(){
  Promise.all([
    post({section:'llm',key:'provider',         value:document.getElementById('llm-provider').value},'ind-ollama'),
    post({section:'llm',key:'model',             value:document.getElementById('ollama-model').value},'ind-ollama'),
    post({section:'llm',key:'base_url',          value:document.getElementById('llm-baseurl').value},'ind-ollama'),
    post({section:'llm',key:'timeout_fetch',     value:+document.getElementById('ollama-tfetch').value},'ind-ollama'),
    post({section:'llm',key:'timeout_report',    value:+document.getElementById('ollama-treport').value},'ind-ollama'),
    post({section:'llm',key:'timeout_edition',   value:+document.getElementById('ollama-tedition').value},'ind-ollama'),
  ]);
}

function saveRss(){
  Promise.all([
    post({section:'rss',key:'max_articles_per_feed',value:+document.getElementById('rss-maxfeed').value},'ind-rss'),
    post({section:'rss',key:'live_window_hours',     value:+document.getElementById('rss-window').value},'ind-rss'),
  ]);
}

function saveRadio(){
  Promise.all([
    post({section:'radio',key:'background_volume',      value:+document.getElementById('radio-bgvol').value},'ind-radio'),
    post({section:'radio',key:'bitrate',                value:document.getElementById('radio-bitrate').value},'ind-radio'),
  ]);
}

function saveWeb(){
  post({section:'web',key:'port',value:+document.getElementById('web-port').value},'ind-web');
}

function saveIcecast(){
  Promise.all([
    post({section:'icecast',key:'host',    value:document.getElementById('ic-host').value},'ind-icecast'),
    post({section:'icecast',key:'port',    value:+document.getElementById('ic-port').value},'ind-icecast'),
    post({section:'icecast',key:'mount',   value:document.getElementById('ic-mount').value},'ind-icecast'),
    post({section:'icecast',key:'password',value:document.getElementById('ic-password').value},'ind-icecast'),
  ]);
}

function savePosts(){
  Promise.all([
    post({section:'posts',key:'nb_posts_per_cycle',value:+document.getElementById('posts-nb').value},'ind-posts'),
    post({section:'posts',key:'window_hours',       value:+document.getElementById('posts-window').value},'ind-posts'),
  ]);
}

// ── Voix TTS ──────────────────────────────────────────────────────────────────
function toggleLang(lang, btn){
  var on = btn.classList.toggle('on');
  btn.classList.toggle('off', !on);
  btn.textContent = on ? '● ON' : '○ OFF';
  var grid = document.getElementById('vg-'+lang);
  if(grid) grid.classList.toggle('disabled', !on);
  if(!on){
    grid.querySelectorAll('.voice-chip').forEach(c=>c.classList.remove('on'));
  } else {
    var first = grid.querySelector('.voice-chip');
    if(first && !grid.querySelector('.voice-chip.on'))
      first.classList.add('on');
  }
}

function toggleVoice(chip){
  chip.classList.toggle('on');
}

function saveVoices(){
  var result = {};
  document.querySelectorAll('.lang-block').forEach(function(block){
    var lang = block.dataset.lang;
    var on   = Array.from(block.querySelectorAll('.voice-chip.on')).map(c=>c.dataset.voice);
    if(on.length) result[lang] = on;
  });
  post({section:'radio',key:'voices',value:result},'ind-voices');
}

// ── Redémarrage ───────────────────────────────────────────────────────────────
function restartEngine(){
  var btn = document.getElementById('btn-restart');
  btn.textContent='⏳ En cours…'; btn.disabled=true;
  fetch('/config/restart',{method:'POST'})
    .then(r=>r.json())
    .then(d=>{
      btn.textContent = d.status==='ok' ? '✓ Redémarré' : '✗ Erreur';
      setTimeout(()=>{ btn.textContent='↺ Redémarrer'; btn.disabled=false; },3000);
    });
}

const actions={saveService,saveOllama,saveRss,saveRadio,saveVoices,saveWeb,saveIcecast,savePosts,restartEngine};
document.addEventListener('click',event=>{
 const button=event.target.closest('[data-action]');if(!button)return;
 const action=button.dataset.action;
 if(Object.hasOwn(actions,action))actions[action]();
 if(action==='showSection')showSection(button.dataset.section,button);
 if(action==='toggleLang')toggleLang(button.dataset.lang,button);
 if(action==='toggleVoice')toggleVoice(button);
 if(action==='home')location.assign('/');
});
const volume=document.getElementById('radio-bgvol');
if(volume)volume.addEventListener('input',()=>document.getElementById('bgvol-val').textContent=Number(volume.value).toFixed(2));
});})();
