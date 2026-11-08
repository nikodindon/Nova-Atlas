// Real same-origin Docker proxy, synthetic private credentials via stdin only.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
let input='';for await(const chunk of process.stdin){input+=chunk;if(input.length>4096)throw Error('fixture input too large');}
const {origin,username,password,day}=JSON.parse(input);
const url=new URL(origin);assert.equal(url.hostname,'127.0.0.1');assert.equal(url.protocol,'http:');
const browser=await chromium.launch({headless:true,args:['--disable-background-networking','--disable-dev-shm-usage']});
try{
 const page=await browser.newPage({viewport:{width:390,height:844}});
 await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
 await page.route('**/stream/**',route=>route.fulfill({status:503,body:''}));
 await page.addInitScript(()=>{
  HTMLMediaElement.prototype.play=function(){this.dispatchEvent(new Event('playing'));return Promise.resolve();};
  HTMLMediaElement.prototype.pause=function(){};HTMLMediaElement.prototype.load=function(){};
 });
 await page.goto(origin);await page.getByRole('heading',{name:'L’actualité en perspective.'}).waitFor();
 await page.locator('figure img[src^="/media/images/"]').first().waitFor();
 assert(await page.locator('figure').first().textContent().then(value=>value.includes('CC BY 4.0')));
 await page.getByRole('button',{name:'Écouter',exact:true}).click();
 await page.evaluate(()=>{window.rcAudio=document.querySelector('audio');});
 const events=await (await page.request.get(origin+'/api/public/events')).json();
 const paths=['/direct','/evenements',...(events.events.length?['/evenements/'+events.events[0].event_id]:[]),'/archives','/archives/'+day,'/rapports/'+day];
 for(const path of paths){
  await page.locator('main').evaluate((_,target)=>{const a=document.createElement('a');a.href=target;document.querySelector('main').append(a);a.click();},path);
  await page.waitForURL(origin+path);assert(await page.evaluate(()=>window.rcAudio===document.querySelector('audio')));
 }
 await page.getByLabel('Canal',{exact:true}).selectOption('android');assert.equal(await page.locator('audio').getAttribute('src'),'/stream/nova-android');
 await page.setViewportSize({width:1440,height:1000});
 await page.goto(origin+'/administration');await page.waitForURL(origin+'/connexion');
 await page.getByLabel('Identifiant',{exact:true}).fill(username);await page.getByLabel('Mot de passe',{exact:true}).fill(password);
 await page.getByRole('button',{name:'Connexion',exact:true}).click();await page.waitForURL(origin+'/administration');
 const csrf=await (await page.request.get(origin+'/admin/csrf')).json();
 const initial=await (await page.request.get(origin+'/api/admin/sources')).json();
 const missing=await page.request.post(origin+'/api/admin/sources',{data:{url:'https://fixture.invalid/rss',category:'rc',version:initial.version}});assert.equal(missing.status(),403);
 const category=await page.request.post(origin+'/api/admin/sources/actions/category',{headers:{'X-CSRF-Token':csrf.csrf_token},data:{category:'rc',version:initial.version}});assert.equal(category.status(),200);
 const current=await category.json();
 const added=await page.request.post(origin+'/api/admin/sources',{headers:{'X-CSRF-Token':csrf.csrf_token},data:{url:'https://fixture.invalid/rss',category:'rc',version:current.version}});assert.equal(added.status(),200);
 for(const path of ['/administration/sources','/administration/radio','/config/feeds/page','/radio-studio','/config/yaml','/index.html','/']){
  const response=await page.goto(origin+path);assert.equal(response.status(),200);assert(!(await page.evaluate(()=>('pwned' in window))));
 }
 for(const path of ['/api/admin/discovery/status','/api/admin/media','/api/admin/radio'])assert.equal((await page.request.get(origin+path)).status(),200);
 const flash=await page.request.get(origin+'/api/flash/status/nonexistent');assert([400,404].includes(flash.status()));
 await page.goto(origin+'/administration');await page.getByRole('button',{name:'Déconnexion',exact:true}).click();await page.waitForURL(origin+'/connexion');
 assert.equal((await page.request.get(origin+'/admin/csrf')).status(),401);
 console.log('RC proxy browser PASS: native navigation/audio DOM, channels, actual Flask login/logout/CSRF, Source Manager add without fetch, discovery/media/radio observations, legacy/native boundary. Audio mocked; no generation or external request.');
}finally{await browser.close();}
