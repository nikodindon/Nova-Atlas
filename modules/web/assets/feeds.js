document.addEventListener('DOMContentLoaded',()=>{
'use strict';
const container=document.getElementById('feeds-container');if(!container)return;
let version='',page=1,preview=null;
const el=id=>document.getElementById(id);
const toast=(message,error=false)=>{const node=el('toast');node.textContent=message;node.className='toast show'+(error?' error':'');setTimeout(()=>node.className='toast',2200);};
function element(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
async function request(path,data,method='POST'){
 const response=await fetch('/api/admin/sources'+path,data===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
 const result=await response.json();if(!response.ok||result.status!=='ok')throw Error(result.msg||'Opération impossible');return result;
}
async function mutate(path,data,method){try{await request(path,{...data,version},method);await refresh();}catch(error){toast(error.message,true);await refresh();}}
function button(text,action){const b=element('button',text,'feed-btn');b.type='button';b.dataset.action=action;return b;}
function render(result){
 container.replaceChildren();
 for(const cat of result.categories){
  const rows=result.sources.filter(s=>s.category===cat);const card=element('section',undefined,'feed-cat');card.append(element('h3',cat));
  for(const source of rows){
   const row=element('div',undefined,'feed-item');row.dataset.id=source.id;row.dataset.cat=cat;row.dataset.url=source.url;
   const title=element('span',(source.name?source.name+' — ':'')+source.url,'feed-url');
   const health=element('div',source.health+' · '+(source.observed_at?new Date(source.observed_at*1000).toLocaleString()+' · âge '+Math.floor(source.observation_age_seconds/3600)+' h'+(source.stale?' (observation ancienne)':''):'jamais observé')+' · HTTP '+(source.http_status||'—')+' · entrées '+(source.entries??'—')+' · échecs '+source.failures+(source.next_check?' · prochaine vérification '+new Date(source.next_check*1000).toLocaleString():'')+(source.error?' · '+source.error:'')+(source.warnings.length?' · '+source.warnings.join(', '):''),'feed-observation');
   row.append(button('↑','up'),button('↓','down'),title,button(source.enabled?'Désactiver':'Réactiver','toggle'),button('Nom','name'),button('Déplacer','category'),button('× Suppr','remove'),health);card.append(row);
  }
  const form=element('form',undefined,'add-form');form.dataset.cat=cat;
  const input=element('input');input.placeholder='https://nouveau-flux.example.com/rss.xml';input.required=true;input.setAttribute('aria-label','Ajouter une URL à '+cat);
  const add=element('button','+ Ajouter','feed-btn');add.type='submit';form.append(input,add);card.append(form);container.append(card);
 }
}
async function refresh(){try{
 const filters=new URLSearchParams({page:String(page),per_page:'50',q:el('source-search').value,category:el('source-category').value,health:el('source-health').value});
 const result=await request('?'+filters);version=result.version;render(result);
 for(const [id,key] of [['stat-cats','categories'],['stat-active','active'],['stat-disabled','disabled']])el(id).textContent=result.stats[key];
 const select=el('source-category'),selected=select.value;select.replaceChildren(new Option('Toutes les catégories',''));for(const cat of result.categories)select.add(new Option(cat,cat));select.value=selected;
 el('source-pagination').textContent='Page '+page+' · '+result.total+' sources';el('source-prev').disabled=page===1;el('source-next').disabled=page*50>=result.total;
}catch(error){toast(error.message,true);}}
container.addEventListener('click',async event=>{
 const b=event.target.closest('button[data-action]');if(!b)return;const row=b.closest('.feed-item');if(!row)return;
 const id=row.dataset.id,action=b.dataset.action;
 try {
 if(action==='remove'){if(confirm('Supprimer définitivement cette source ?'))await mutate('/'+id,{},'DELETE');}
 else if(action==='toggle'){
  const result=await request('/'+id);await mutate('/'+id,{enabled:!result.source.enabled},'PATCH');
 }else if(action==='up'||action==='down')await mutate('/actions/move',{id,direction:action});
 else if(action==='name'){const name=prompt('Nom de la source');if(name!==null)await mutate('/'+id,{name},'PATCH');}
 else if(action==='category'){const category=prompt('Catégorie de destination existante');if(category!==null)await mutate('/actions/move',{id,category});}
 }catch(error){toast(error.message,true);}
});
container.addEventListener('submit',async event=>{event.preventDefault();const form=event.target;await mutate('',{category:form.dataset.cat,url:form.querySelector('input').value});});
el('new-cat-form').addEventListener('submit',async event=>{event.preventDefault();await mutate('/actions/category',{category:el('new-cat-name').value});});
el('source-filter').addEventListener('submit',event=>{event.preventDefault();page=1;refresh();});
el('source-prev').addEventListener('click',()=>{page--;refresh();});el('source-next').addEventListener('click',()=>{page++;refresh();});
el('source-test-form').addEventListener('submit',async event=>{event.preventDefault();const b=event.target.querySelector('button');b.disabled=true;try{const result=await request('/test',{url:el('source-test-url').value});el('source-test-result').textContent=JSON.stringify(result.result,null,2);}catch(error){toast(error.message,true);}finally{b.disabled=false;}});
el('source-import-form').addEventListener('submit',async event=>{event.preventDefault();preview=null;el('source-import-apply').disabled=true;try{
 const content=el('source-import-content').value,format=el('source-import-format').value;const result=await request('/import',{content,format});preview={content,format,version:result.version,fingerprint:result.fingerprint,preview_token:result.preview_token};el('source-import-result').textContent=JSON.stringify(result,null,2);el('source-import-apply').disabled=false;
}catch(error){toast(error.message,true);}});
for(const id of ['source-import-content','source-import-format'])el(id).addEventListener('input',()=>{preview=null;el('source-import-apply').disabled=true;});
el('source-import-apply').addEventListener('click',async()=>{if(!preview)return;try{await request('/import',{...preview,apply:true});preview=null;el('source-import-apply').disabled=true;await refresh();toast('Import terminé');}catch(error){toast(error.message,true);}});
refresh();
});
