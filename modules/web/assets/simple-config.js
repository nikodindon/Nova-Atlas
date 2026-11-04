document.addEventListener("DOMContentLoaded",()=>{
if(!document.getElementById("configForm"))return;

document.getElementById('configForm').addEventListener('submit',async(e)=>{
  e.preventDefault();
  const data=Object.fromEntries(new FormData(e.target));
  ['fetch_timeout','edition_timeout','max_articles_feed','live_window_hours'].forEach(k=>{if(data[k])data[k]=parseInt(data[k]);});
  const r=await fetch('/config/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(r.ok){
    document.getElementById('saveMsg').style.display='block';
    setTimeout(()=>document.getElementById('saveMsg').style.display='none',4000);
    document.querySelector('.current-tag').textContent=data.ollama_model;
  }
});

});
