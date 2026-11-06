(function(){
'use strict';
const selected = new Set();
let refreshTimer = null;
let navigation = null;
let hiddenCategories = new Set();
const audio = window._novaAudio || (window._novaAudio = new Audio());
audio.preload = 'none';
function stored(key){try{return localStorage.getItem(key);}catch(_){return null;}}
function store(key,value){try{localStorage.setItem(key,value);}catch(_){}}
function theme(){
 const light=stored('nova-theme')==='light';document.body.classList.toggle('light',light);
 const button=document.querySelector('.theme-btn');if(button)button.textContent=light?'☀️':'🌙';
}
function syncPlayer(){
 const button=document.getElementById('radio-btn'),wave=document.getElementById('radio-wave'),label=document.getElementById('radio-label');
 if(button)button.textContent=audio.paused?'▶':'⏸';
 if(wave)wave.classList.toggle('playing',!audio.paused);
 if(label)label.textContent=audio.paused?(document.body.dataset.radioLabel||'RADIO'):'EN DIRECT';
}
function streamURL(value){
 try{const url=new URL(value,location.href);return url.origin===location.origin&&url.pathname.startsWith('/stream/')?url.href:null;}catch(_){return null;}
}
function radio(button){
 const url=streamURL(button.dataset.stream);if(!url)return;
 if(!audio.paused){audio.pause();store('nova-radio-playing','0');return;}
 if(audio.src!==url)audio.src=url;
 store('nova-radio-url',url);
 audio.play().then(()=>store('nova-radio-playing','1')).catch(()=>{});
}
audio.addEventListener('play',syncPlayer);audio.addEventListener('pause',syncPlayer);
audio.addEventListener('error',()=>setTimeout(()=>{
 if(stored('nova-radio-playing')==='1'&&streamURL(audio.src)){audio.load();audio.play().catch(()=>{});}
},3000));
function filters(){
 document.querySelectorAll('#live-grid [data-category]').forEach(item=>{
  const category=item.dataset.category;
  const hide=(selected.size>0&&!selected.has(category))||hiddenCategories.has(category);
  item.hidden=hide;item.setAttribute('aria-hidden',String(hide));
 });
 document.querySelectorAll('.filter-btn').forEach(button=>{
  const active=button.dataset.cat==='__all'?selected.size===0:selected.has(button.dataset.cat);
  button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
 });
}
async function preferences(){
 try{const result=await fetch('/api/preferences');if(!result.ok)return;
  const data=await result.json();hiddenCategories=new Set(data.preferences?.hidden_categories||[]);filters();
  document.querySelectorAll('[data-category]').forEach(item=>{if(hiddenCategories.has(item.dataset.category))item.hidden=true;});
  const bar=document.getElementById('prefs-bar');if(bar&&hiddenCategories.size){
   bar.textContent=hiddenCategories.size+' catégorie(s) cachée(s) — ';
   const link=document.createElement('a');link.href='/preferences';link.textContent='Gérer';bar.append(link);bar.style.display='block';
  }
 }catch(_){}
}
function publicURL(value){
 try{const url=new URL(value,location.href);return url.origin===location.origin&&
  (url.pathname==='/config/feeds/page'||url.pathname==='/radio-studio'||/^\/(?:live(?:\.html)?|archives|index\.html)?$/.test(url.pathname)||/^\/\d{8}_report\.html$/.test(url.pathname)||/^\/editions\/\d{8}_(matin|midi|soir)\.html$/.test(url.pathname))?url:null;}catch(_){return null;}
}
function inertDocument(html){
 const doc=new DOMParser().parseFromString(html,'text/html');
 doc.querySelectorAll('script,iframe,object,embed,base,meta,link').forEach(node=>node.remove());
 doc.querySelectorAll('*').forEach(node=>{for(const attribute of [...node.attributes]){
  if(/^on/i.test(attribute.name)||attribute.name==='srcdoc')node.removeAttribute(attribute.name);
  if(attribute.name==='href'){try{const url=new URL(attribute.value,location.href);if(!['http:','https:'].includes(url.protocol))node.removeAttribute(attribute.name);}catch(_){node.removeAttribute(attribute.name);}}
 }});
 return doc;
}
function replaceContents(current,next){if(current&&next)current.replaceChildren(...[...next.childNodes].map(node=>document.importNode(node,true)));}
async function navigate(url,push=true){
 if(navigation)navigation.abort();const request=navigation=new AbortController();
 try{
  const response=await fetch(url.href,{signal:request.signal});if(!response.ok||!response.headers.get('content-type')?.includes('text/html'))throw Error('Invalid page');
  if(response.url&&!publicURL(response.url))throw Error('Invalid destination');
  const doc=inertDocument(await response.text());if(request!==navigation)return;
  const page=doc.querySelector('.page'),current=document.querySelector('.page');if(!page||!current)throw Error('Missing page');
  replaceContents(current,page);replaceContents(document.querySelector('.topbar-nav'),doc.querySelector('.topbar-nav'));
  const banner=document.querySelector('.breaking-banner'),nextBanner=doc.querySelector('.breaking-banner');
  if(banner)banner.remove();if(nextBanner)current.before(document.importNode(nextBanner,true));
  const style=document.head.querySelector('style'),newStyle=doc.head.querySelector('style');if(style&&newStyle)style.textContent=newStyle.textContent;
  document.title=doc.title;document.body.dataset.page=doc.body.dataset.page||'';
  if(push)history.pushState({},'',url.href);initPage();window.scrollTo(0,0);
 }catch(error){if(error.name!=='AbortError')location.assign(url.href);}
}
function startLive(){
 if(refreshTimer)clearTimeout(refreshTimer);refreshTimer=null;
 if(document.body.dataset.page!=='live')return;
 refreshTimer=setTimeout(async()=>{
  try{const response=await fetch(location.href);if(!response.ok)throw Error('refresh');
   const doc=inertDocument(await response.text());if(document.body.dataset.page!=='live')return;
   replaceContents(document.getElementById('live-grid'),doc.getElementById('live-grid'));
   replaceContents(document.getElementById('editorial-events'),doc.getElementById('editorial-events'));
   replaceContents(document.querySelector('.filter-bar'),doc.querySelector('.filter-bar'));
   const stamp=document.getElementById('live-updated'),next=doc.getElementById('live-updated');if(stamp&&next)stamp.textContent=next.textContent;
   filters();
  }catch(_){}finally{startLive();}
 },120000);
}
function initPage(){theme();syncPlayer();filters();preferences();startLive();if(window.NovaSources)window.NovaSources();if(window.NovaStudio)window.NovaStudio();}
document.addEventListener('click',event=>{
 const button=event.target.closest('[data-action]');
 if(button){switch(button.dataset.action){
  case 'theme':store('nova-theme',stored('nova-theme')==='light'?'dark':'light');theme();break;
  case 'radio-toggle':radio(button);break;
  case 'sources':{const list=document.getElementById('sources-list');if(list){const open=list.classList.toggle('open');button.classList.toggle('open',open);button.setAttribute('aria-expanded',String(open));}break;}
  case 'summary':{const summary=document.getElementById(button.dataset.summary);if(summary){const open=summary.classList.toggle('expanded');button.classList.toggle('open',open);button.setAttribute('aria-expanded',String(open));button.querySelector('.tgl-label').textContent=open?button.dataset.less:button.dataset.more;}break;}
 }}
 const filter=event.target.closest('.filter-btn');if(filter){const cat=filter.dataset.cat;if(cat==='__all')selected.clear();else if(selected.has(cat))selected.delete(cat);else selected.add(cat);filters();}
 const link=event.target.closest('a');if(!link||event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||link.target==='_blank'||link.hasAttribute('download'))return;
 const url=publicURL(link.getAttribute('href'));if(!url||url.hash||!document.querySelector('.page'))return;
 event.preventDefault();navigate(url);
});
window.addEventListener('popstate',()=>{const url=publicURL(location.href);if(url&&document.querySelector('.page'))navigate(url,false);else location.reload();});
document.addEventListener('DOMContentLoaded',()=>{
 initPage();const url=streamURL(stored('nova-radio-url'));
 if(stored('nova-radio-playing')==='1'&&url){audio.src=url;audio.play().catch(()=>{});}
});
})();
