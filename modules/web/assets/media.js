window.NovaMedia=function(){
'use strict';
const root=document.getElementById('media-manager');if(!root||root.dataset.initialized)return;root.dataset.initialized='1';
const el=id=>root.querySelector('[id="'+id+'"]');let version='',records=[],targets=[],page=1;
function node(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;}
function status(value){el('media-state').textContent=value;}
async function api(path,body){const response=await fetch('/api/admin/media'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok||data.status!=='ok')throw Error(data.msg||'Opération refusée');return data;}
function target(){const value=targets[Number(el('media-target').value)];if(!value)throw Error('Choisir une cible');return {kind:value.kind,id:value.id,fingerprint:value.fingerprint};}
async function refresh(){try{
 const result=await api('?page='+page);version=result.version;records=result.records;status('Mode : '+result.mode+' · '+result.count+' candidats. Aucune publication sans approbation et droits établis.');
 const container=el('media-records');container.replaceChildren();
 for(const r of records){
  const card=node('article');card.className='feed-cat';card.dataset.media=r.id;
  card.append(node('h3',r.candidate.title||r.candidate.url),node('p',r.status+' · '+r.target.kind+' · '+r.target.title),node('p','Auteur : '+(r.rights.author||'inconnu')+' · Licence : '+(r.rights.license||'inconnue')),node('p','Page source : '+r.candidate.source_page),node('p','Conditions : '+(r.rights.license_url||'inconnues')),node('p',r.rights.obligations.join(', ')),node('p',r.rights.restrictions.join(', ')));
  if(r.error)card.append(node('p',r.error));
  if(r.artifact){const image=node('img');image.src='/api/admin/media/'+r.id+'/preview/320.jpg';image.width=r.artifact.variants[0].width;image.height=r.artifact.variants[0].height;image.loading='lazy';image.alt='Prévisualisation privée, illustration générique';card.append(image);}
  const caption=node('input');caption.value=r.caption;caption.setAttribute('aria-label','Légende');caption.maxLength=500;card.append(caption);
  const label=node('label');const ack=node('input');ack.type='checkbox';ack.className='media-ack';label.append(ack,document.createTextNode(' J’ai examiné la ressource, les droits, l’attribution et les éventuels droits de tiers ; usage générique.'));card.append(label);
  for(const [action,text] of [['refresh','Revérifier Commons'],['prepare','Préparer'],['caption','Modifier la légende'],['approve','Approuver'],['reject','Refuser'],['disable','Désactiver']]){const b=node('button',text);b.type='button';b.className='feed-btn';b.dataset.mediaAction=action;b.disabled=(action==='prepare'&&r.rights.status!=='rights_verified')||(action==='approve'&&(!r.artifact||r.rights.status!=='rights_verified'));card.append(b);}
  container.append(card);
 }
 el('media-prev').disabled=page===1;el('media-next').disabled=page*20>=result.count;
}catch(error){status(error.message);}}
root.addEventListener('click',async event=>{const b=event.target.closest('[data-media-action]');if(!b)return;const card=b.closest('[data-media]'),record=records.find(r=>r.id===card.dataset.media),action=b.dataset.mediaAction;
 b.disabled=true;try{const payload=action==='refresh'?{}:{version};if(action==='approve'){payload.binding=record.approval_binding;payload.ack=card.querySelector('.media-ack').checked;}if(action==='caption')payload.caption=card.querySelector('input[aria-label="Légende"]').value;
 await api('/'+record.id+'/'+action,payload);await refresh();}catch(error){status(error.message+' — recharger en cas de conflit.');b.disabled=false;}});
el('media-search-form').addEventListener('submit',async event=>{event.preventDefault();try{status('Recherche Commons explicite…');await api('/search',{query:el('media-query').value,target:target()});await refresh();}catch(error){status(error.message);}});
for(const [id,flag] of [['media-rss',false],['media-html',true]])el(id).addEventListener('click',async()=>{try{await api('/discover',{target:target(),page:flag});await refresh();}catch(error){status(error.message);}});
el('media-cancel').addEventListener('click',async()=>{try{await api('/cancel',{});status('Annulation demandée');}catch(error){status(error.message);}});
el('media-prev').addEventListener('click',()=>{page--;refresh();});el('media-next').addEventListener('click',()=>{page++;refresh();});
api('/targets').then(result=>{targets=result.targets;targets.forEach((t,i)=>el('media-target').add(new Option(t.kind+' : '+t.title,String(i))));}).catch(error=>status(error.message));
refresh();
};
