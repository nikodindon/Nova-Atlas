document.addEventListener('DOMContentLoaded',()=>{
'use strict';
const container=document.getElementById('prefs-container');if(!container)return;
let categories=[],hidden=new Set();
function toast(message){const node=document.getElementById('toast');node.textContent=message;node.className='toast show';setTimeout(()=>node.className='toast',1800);}
function render(){container.replaceChildren();for(const cat of categories){
 const card=document.createElement('div');card.className='pref-card'+(hidden.has(cat)?' hidden':'');
 const label=document.createElement('label'),checkbox=document.createElement('input'),name=document.createElement('span');
 checkbox.type='checkbox';checkbox.checked=!hidden.has(cat);checkbox.dataset.cat=cat;name.className='cat-name';name.textContent=cat.replace(/_/g,' ');
 label.append(checkbox,name);card.append(label);container.append(card);
}document.getElementById('visible-count').textContent=categories.length-hidden.size;document.getElementById('total-count').textContent=categories.length;}
container.addEventListener('change',async event=>{const cat=event.target.dataset.cat;if(!cat)return;
 try{const response=await fetch('/api/preferences/toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category:cat})});const result=await response.json();
 if(result.status==='ok'){if(result.hidden)hidden.add(cat);else hidden.delete(cat);render();toast('Préférence enregistrée');}else toast('Opération impossible');}catch(_){toast('Opération impossible');}
});
document.getElementById('reset-btn').addEventListener('click',async()=>{if(!confirm('Réinitialiser les catégories ?'))return;try{const result=await fetch('/api/preferences/reset',{method:'POST'}).then(r=>r.json());if(result.status==='ok'){hidden.clear();render();}}catch(_){toast('Opération impossible');}});
(async()=>{try{const [prefs,feeds]=await Promise.all([fetch('/api/preferences').then(r=>r.json()),fetch('/config/feeds').then(r=>r.json())]);if(prefs.status!=='ok'||feeds.status!=='ok')throw Error();categories=Object.keys(feeds.feeds).sort();hidden=new Set(prefs.preferences.hidden_categories||[]);render();}catch(_){toast('Erreur de chargement');}})();
});
