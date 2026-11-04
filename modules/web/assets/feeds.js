document.addEventListener('DOMContentLoaded',()=>{
'use strict';
const container=document.getElementById('feeds-container');if(!container)return;
const toast=(message,error=false)=>{const node=document.getElementById('toast');node.textContent=message;node.className='toast show'+(error?' error':'');setTimeout(()=>node.className='toast',2200);};
async function request(action,data){const response=await fetch('/config/feeds'+action,data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{});return response.json();}
function element(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
function linkURL(value){try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password&&!/[\s\\]/.test(value)?url.href:null;}catch(_){return null;}}
function render(cats){
 container.replaceChildren();
 for(const [cat,urls] of Object.entries(cats)){
  const card=element('div',undefined,'feed-cat');card.append(element('h3',cat+' ('+urls.length+')'));
  urls.forEach((url,index)=>{
   const row=element('div',undefined,'feed-item');row.dataset.url=url;row.dataset.cat=cat;
   const up=element('button','↑','feed-btn');up.dataset.action='up';up.disabled=index===0;
   const down=element('button','↓','feed-btn');down.dataset.action='down';down.disabled=index===urls.length-1;
   const link=element('a',String(url).replace(/^https?:\/\//,'').slice(0,60),'feed-url');
   const href=linkURL(url);if(href)link.href=href;link.target='_blank';link.rel='noopener noreferrer';link.title=url;
   const toggle=element('button','Désactiver','feed-btn');toggle.dataset.action='toggle';
   const remove=element('button','× Suppr','feed-btn danger');remove.dataset.action='remove';
   row.append(up,down,link,toggle,remove);card.append(row);
  });
  const form=element('form',undefined,'add-form');form.dataset.cat=cat;
  const input=element('input');input.placeholder='https://nouveau-flux.example.com/rss.xml';input.required=true;
  const add=element('button','+ Ajouter','feed-btn');add.type='submit';form.append(input,add);card.append(form);container.append(card);
 }
}
async function refresh(){try{const result=await request('');if(result.status!=='ok')throw Error();render(result.feeds);
 for(const [id,key] of [['stat-cats','categories'],['stat-active','active'],['stat-disabled','disabled']])document.getElementById(id).textContent=result.stats[key];
}catch(_){toast('Erreur de chargement',true);}}
container.addEventListener('click',async event=>{
 const button=event.target.closest('button[data-action]');if(!button)return;const row=button.closest('.feed-item');if(!row)return;
 const action=button.dataset.action;if(action==='remove'&&!confirm('Supprimer ce flux ?'))return;
 try{const data={category:row.dataset.cat,url:row.dataset.url};if(action==='up'||action==='down')data.direction=action;
 const result=await request(action==='up'||action==='down'?'/move':'/'+action,data);if(result.status==='ok')refresh();else toast(result.msg||'Erreur',true);}catch(_){toast('Opération impossible',true);}
});
container.addEventListener('submit',async event=>{event.preventDefault();const form=event.target;try{const result=await request('/add',{category:form.dataset.cat,url:form.querySelector('input').value.trim()});if(result.status==='ok'){toast('Flux ajouté');refresh();}else toast(result.msg||'Erreur',true);}catch(_){toast('Opération impossible',true);}});
document.getElementById('new-cat-form').addEventListener('submit',async event=>{event.preventDefault();try{const result=await request('/category',{category:document.getElementById('new-cat-name').value.trim()});if(result.status==='ok'){event.target.reset();refresh();}else toast(result.msg||'Erreur',true);}catch(_){toast('Opération impossible',true);}});
refresh();
});
