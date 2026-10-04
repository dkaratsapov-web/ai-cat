"""HTML-страница панели (одним файлом, без внешних зависимостей)."""

PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>МяуРкетинг · Студия</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#16181d;--muted:#6b7280;--line:#e5e7eb;--accent:#ffd43b;--accent-ink:#1a1a1a;
--ok:#16a34a;--warn:#d97706;--bad:#dc2626;--chip:#f1f3f5;--shadow:0 1px 2px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--card:#1a1d24;--ink:#eceef2;--muted:#9aa3b2;--line:#2a2f3a;
--chip:#232733;--shadow:none}}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink)}
header{display:flex;align-items:center;gap:12px;padding:14px 22px;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:5}
header .logo{font-weight:800;font-size:18px}header .logo b{background:var(--accent);color:var(--accent-ink);padding:2px 8px;border-radius:8px}
header .sp{flex:1}.budget{font-size:13px;color:var(--muted)}
.wrap{display:grid;grid-template-columns:280px 1fr;min-height:calc(100vh - 58px)}
aside{border-right:1px solid var(--line);padding:16px;background:var(--card)}
aside h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:6px 0 10px}
.ep{padding:10px 12px;border-radius:10px;cursor:pointer;margin-bottom:4px;border:1px solid transparent}
.ep:hover{background:var(--chip)}.ep.active{border-color:var(--accent);background:var(--chip)}
.ep .t{font-weight:600;font-size:14px}.ep .s{font-size:12px;color:var(--muted)}
.new{margin-top:16px;display:flex;flex-direction:column;gap:8px}
select,textarea,input{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px}
main{padding:22px 26px 140px;max-width:1400px}
h1{margin:0 0 4px;font-size:24px}.sub{color:var(--muted);margin-bottom:16px}
.steps{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0 18px}
.step{padding:6px 12px;border-radius:999px;background:var(--chip);font-size:13px;color:var(--muted)}
.step.done{color:var(--ok)}.step.cur{background:var(--accent);color:var(--accent-ink);font-weight:700}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow);margin-bottom:18px}
.bar .info{flex:1;min-width:220px}.bar .info b{font-size:16px}
button{font:inherit;font-weight:600;border:0;border-radius:10px;padding:10px 16px;cursor:pointer;background:var(--chip);color:var(--ink)}
button.primary{background:var(--accent);color:var(--accent-ink)}button.danger{background:#fee2e2;color:#991b1b}
button:disabled{opacity:.5;cursor:not-allowed}button.sm{padding:6px 10px;font-size:13px}
.warn{background:#fff7e6;color:#8a5300;border-radius:10px;padding:10px 14px;margin-bottom:14px;font-size:14px}
@media (prefers-color-scheme:dark){.warn{background:#3a2a10;color:#ffd58a}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.sc{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:var(--shadow);display:flex;flex-direction:column}
.thumb{aspect-ratio:9/16;background:#0d0f14;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative}
.thumb img,.thumb video{width:100%;height:100%;object-fit:cover}
.thumb .card{color:#fff;padding:18px;font-size:14px;align-self:stretch;width:100%}
.thumb .card h4{margin:30px 0 12px;font-size:18px}.thumb .card li{margin:6px 0}
.badge{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.65);color:#fff;font-size:12px;padding:3px 8px;border-radius:999px}
.badge.r{left:auto;right:8px}.badge.paid{background:var(--accent);color:#111}
.body{padding:12px;display:flex;flex-direction:column;gap:8px;flex:1}
.meta{font-size:12px;color:var(--muted)}.vo{font-size:14px}
.chips{display:flex;gap:6px;flex-wrap:wrap}.chip{font-size:12px;background:var(--chip);border-radius:999px;padding:2px 8px}
.chip.ok{color:var(--ok)}.chip.bad{color:var(--bad)}.chip.run{color:var(--warn)}
audio{width:100%;height:32px}
textarea{width:100%;min-height:70px;resize:vertical}
.row{display:flex;gap:6px;flex-wrap:wrap}
.final{display:grid;grid-template-columns:minmax(260px,360px) 1fr;gap:18px;margin-top:22px}
.final video{width:100%;border-radius:14px;background:#000}
pre{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;font-size:12.5px;max-height:420px;overflow:auto}
.log{position:fixed;left:0;right:0;bottom:0;background:#0f1117;color:#d6dae3;font:12.5px/1.45 ui-monospace,Consolas,monospace;max-height:34vh;overflow:auto;padding:10px 18px;border-top:3px solid var(--accent);display:none;z-index:9}
.log .h{font:600 13px system-ui;color:#fff;margin-bottom:6px;display:flex;gap:10px;align-items:center}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:20}
.modal .box{background:var(--card);border-radius:16px;padding:22px;max-width:460px;width:92%}
.modal h3{margin:0 0 8px}.modal .price{font-size:28px;font-weight:800;margin:10px 0}
.empty{color:var(--muted);padding:40px;text-align:center}
.covers img{height:220px;border-radius:10px;margin-right:8px}
@media (max-width:900px){.wrap{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid var(--line)}.final{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><div class="logo"><b>Мяу</b>Ркетинг · Студия</div><div class="sp"></div><div class="budget" id="budget"></div></header>
<div class="wrap">
<aside>
  <h3>Ролики</h3><div id="eps"></div>
  <div class="new"><h3>Новый ролик</h3><select id="tpl"></select><button onclick="newEp()">Создать из шаблона</button>
  <div class="meta">Новые сценарии по вашему заданию пишет Claude в чате — они появятся в этом списке.</div></div>
</aside>
<main id="main"><div class="empty">Выберите ролик слева</div></main>
</div>
<div class="log" id="log"><div class="h"><span id="logt"></span><span class="sp" style="flex:1"></span><button class="sm" onclick="hideLog()">Скрыть</button></div><div id="logb"></div></div>
<div class="modal" id="modal"><div class="box"><h3 id="mt"></h3><div id="md" class="meta"></div><div class="price" id="mp"></div>
<div class="row"><button class="primary" id="mok">Подтвердить</button><button onclick="closeModal()">Отмена</button></div></div></div>
<script>
const TOKEN="__TOKEN__";let cur=null,ep=null,pollT=null,watching=null;
const $=s=>document.querySelector(s);
const esc=s=>(s??"").toString().replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const usd=v=>"$"+(+v||0).toFixed(2);
async function api(u,body){const o=body?{method:"POST",headers:{"Content-Type":"application/json","X-Token":TOKEN},body:JSON.stringify(body)}:{};
 const r=await fetch(u,o);const j=await r.json();if(!r.ok||j&&j.error)throw new Error(j.error||r.status);return j}
async function loadEps(){const eps=await api("/api/episodes");$("#eps").innerHTML=eps.filter(e=>!e.mock).map(e=>
 `<div class="ep ${e.id===cur?"active":""}" onclick="openEp('${e.id}')"><div class="t">${esc(e.title)}</div><div class="s">${esc(e.id.split("-").slice(0,2).join("-"))} · ${esc(e.status_ru)}</div></div>`).join("")||'<div class="meta">Пока нет роликов</div>';
 const t=await api("/api/templates");$("#tpl").innerHTML=t.map(x=>`<option value="${x.id}">${esc(x.title)} (${esc(x.type)})</option>`).join("")}
async function newEp(){try{const r=await api("/api/new",{template:$("#tpl").value});await loadEps();openEp(r.id)}catch(e){alert(e.message)}}
async function openEp(id){cur=id;await loadEps();ep=await api("/api/episode/"+id);render()}
const STEPS=[["draft","Сценарий"],["approved","Озвучка"],["voiced","Сцены"],["generated","Монтаж"],["assembled","Проверка"],["qa_passed","Утверждение"],["final_approved","Публикация"],["packaged","Готово"]];
function stageIdx(s){if(s==="qa_failed")return 4;const i=STEPS.findIndex(x=>x[0]===s);return i<0?0:i}
function nextAction(){const s=ep.status,e=ep.estimate,rub=(e.voice_usd*90);
 if(s==="cancelled")return{info:"Производство отменено. Отредактируйте сценарий и утвердите заново."};
 if(!ep.approved)return{info:"Проверьте тексты и кадры сцен, затем утвердите сценарий. Без этого платные шаги заблокированы.",btn:"Утвердить сценарий",act:"approve_script"};
 if(s==="approved")return{info:"Озвучка всех фраз голосом кота (Yandex SpeechKit).",btn:`Озвучить · ~${Math.max(1,Math.round(rub))} ₽`,act:"voice",paid:true,price:`~${Math.max(1,Math.round(rub))} ₽`};
 if(s==="voiced"){const n=ep.scenes.filter(x=>x.paid&&!x.has_clip).length;
  return{info:`Генерация ${n} AI-сцен в Kling. Можно сначала протестировать одну сцену кнопкой на её карточке.`,btn:`Сгенерировать всё · ${usd(e.video_usd)}`,act:"generate",paid:true,price:usd(e.video_usd)}}
 if(s==="generated")return{info:"Все сцены готовы — собираем ролик (бесплатно).",btn:"Собрать ролик",act:"assemble"};
 if(s==="assembled")return{info:"Автоматическая техническая проверка (бесплатно).",btn:"Проверить",act:"qa"};
 if(s==="qa_failed")return{info:"Проверка нашла проблемы — см. отчёт ниже. Переделайте сцену или соберите заново.",btn:"Собрать заново",act:"assemble"};
 if(s==="qa_passed")return{info:"Посмотрите ролик ниже. Если всё нравится — утвердите.",btn:"Утвердить ролик",act:"approve_final"};
 if(s==="final_approved")return{info:"Обложки, заголовки, описание и хештеги.",btn:"Подготовить публикацию",act:"package"};
 return{info:"Ролик готов к публикации — материалы ниже."}}
function render(){const e=ep,n=nextAction(),si=stageIdx(e.status);
 $("#budget").textContent=`Бюджет месяца: ${usd(e.budget.spent_month_usd)} из ${usd(e.budget.monthly_budget_usd)} · на ролик: ${usd(e.budget.spent_episode_usd)} из ${usd(e.budget.per_video_limit_usd)}`;
 let h=`<h1>${esc(e.title)}</h1><div class="sub">${esc(e.content_type)} · ~${e.total_duration} с · ${esc(e.id)}</div>`;
 h+=`<div class="steps">${STEPS.map((x,i)=>`<span class="step ${i<si?"done":i===si?"cur":""}">${i<si?"✓ ":""}${x[1]}</span>`).join("")}</div>`;
 h+=`<div class="bar"><div class="info"><b>${esc(e.status_ru)}</b><div class="meta">${esc(n.info)}</div></div>`+
   (n.btn?`<button class="primary" onclick="doAct('${n.act}',${!!n.paid},'${esc(n.price||"")}')">${esc(n.btn)}</button>`:"")+
   `<button onclick="doAct('refresh')">Обновить статус</button>${["voiced","generated","assembled","qa_failed","qa_passed"].includes(e.status)?`<button onclick="doAct('assemble')">Пересобрать</button>`:""}</div>`;
 if(e.disclaimer)h+=`<div class="warn">Дисклеймер: ${esc(e.disclaimer)}</div>`;
 if(e.warnings.length)h+=`<div class="warn">${e.warnings.map(esc).join("<br>")}</div>`;
 if(e.estimate.error)h+=`<div class="warn">Смета: ${esc(e.estimate.error)}</div>`;
 h+=`<div class="grid">${e.scenes.map(sceneCard).join("")}</div>`;
 if(e.final_video){h+=`<div class="final"><div><video controls playsinline src="/media/${e.id}/output/${encodeURIComponent(e.final_video)}?t=${Date.now()}"></video></div><div>`+
   (e.qa?`<h3>Техническая проверка</h3><pre>${esc(e.qa)}</pre>`:"")+`</div></div>`}
 if(e.publish){const p=e.publish;h+=`<h3>Публикация</h3><div class="covers">${e.covers.map(c=>`<img src="/media/${e.id}/publish/${c}">`).join("")}</div>
   <pre>${esc((p.titles||[]).join("\n"))}\n\n${esc(p.description)}\n\n${esc(p.cta)}\n\n${esc((p.hashtags||[]).join(" "))}</pre><div class="meta">Файлы: projects/${esc(e.id)}/publish/. Публикуете вы сами.</div>`}
 $("#main").innerHTML=h}
function sceneCard(s){let t;
 if(s.has_clip)t=`<video src="/media/${ep.id}/scenes/${encodeURIComponent(s.clip)}?t=${Date.now()}" muted loop playsinline controls></video>`;
 else if(s.local_image)t=`<img src="/img/${ep.id}/${encodeURIComponent(s.local_image)}">`;
 else if(s.reference_file)t=`<img src="/ref/${s.reference_file}">`;
 else if(s.local_kind==="card"||s.local_kind==="chart")t=`<div class="card"><h4>${esc(s.title||"")}</h4><ul>${(s.bullets||[]).map(b=>`<li>${esc(b)}</li>`).join("")}</ul></div>`;
 else t=`<div class="card">${esc(s.visual)}</div>`;
 const est=s.estimate?`<span class="chip">${usd(s.estimate.usd)}${s.estimate.reused?" · уже оплачено":""}</span>`:"";
 const job=s.job?`<span class="chip ${s.job.status==="succeeded"?"ok":s.job.status==="failed"?"bad":"run"}">${esc(s.job.status)}</span>`:"";
 const err=s.job&&s.job.status==="failed"&&s.job.error?`<div class="meta" style="color:var(--bad)">${esc(s.job.error)}</div>`:"";
 const gen=s.paid&&["voiced","generated","assembled","qa_failed","qa_passed"].includes(ep.status)&&ep.approved?
   (s.has_clip?`<button class="sm" onclick="regen('${s.id}')">Переделать сцену</button>`:`<button class="sm primary" onclick="genOne('${s.id}')">Сгенерировать эту</button>`):"";
 return `<div class="sc"><div class="thumb">${t}<span class="badge">${s.id} · ${s.start}с</span><span class="badge r ${s.paid?"paid":""}">${s.paid?"Kling":"бесплатно"}</span></div>
 <div class="body"><div class="meta">${esc(s.type_ru)}${s.reference?" · "+esc(s.reference):""} · ${s.duration} с</div>
 <textarea id="vo-${s.id}">${esc(s.voiceover)}</textarea>
 <div class="row"><button class="sm" onclick="saveVo('${s.id}')">Сохранить текст</button>${gen}</div>
 ${s.has_audio?`<audio controls preload="none" src="/media/${ep.id}/audio/${s.id}.wav?t=${Date.now()}"></audio>`:""}
 <div class="chips">${est}${job}${s.has_audio?'<span class="chip ok">озвучено</span>':""}${s.has_clip?'<span class="chip ok">видео есть</span>':""}</div>${err}
 ${s.visual?`<div class="meta">Кадр: ${esc(s.visual)}</div>`:""}</div></div>`}
async function saveVo(id){const v=$("#vo-"+id).value;if(!confirm("Сохранить текст? Сценарий потребует повторного утверждения, сцену нужно будет переозвучить."))return;
 try{await api(`/api/episode/${ep.id}/action`,{action:"edit_scene",scene:id,voiceover:v});openEp(ep.id)}catch(e){alert(e.message)}}
function genOne(id){const s=ep.scenes.find(x=>x.id===id);ask(`Сгенерировать сцену ${id}`,`Тест одной сцены в Kling.`,usd(s.estimate?s.estimate.usd:0),()=>run("generate",{scenes:[id],confirm:true}))}
function regen(id){const s=ep.scenes.find(x=>x.id===id);ask(`Переделать сцену ${id}`,`Новая платная попытка. Прошлый вариант сохранится как .prev.`,usd(s.estimate?s.estimate.usd:0),()=>run("generate",{scenes:[id],regenerate:[id],confirm:true}))}
function doAct(act,paid,price){if(paid){ask(act==="voice"?"Озвучка":"Генерация сцен",act==="voice"?"Yandex SpeechKit, оплата по факту.":"Kling. Готовые сцены повторно не оплачиваются.",price,()=>run(act,{confirm:true}));return}
 run(act,{})}
function ask(t,d,p,fn){$("#mt").textContent=t;$("#md").textContent=d;$("#mp").textContent=p;$("#modal").style.display="flex";$("#mok").onclick=()=>{closeModal();fn()}}
function closeModal(){$("#modal").style.display="none"}
async function run(act,extra){try{const r=await api(`/api/episode/${ep.id}/action`,{action:act,...extra});
 if(r.task){watching=r.task;showLog();poll()}else openEp(ep.id)}catch(e){alert(e.message)}}
function showLog(){$("#log").style.display="block"}function hideLog(){$("#log").style.display="none"}
async function poll(){clearTimeout(pollT);if(!watching)return;const t=await api("/api/task/"+watching);if(!t)return;
 $("#logt").textContent=`${t.title}: ${t.status==="running"?"выполняется…":t.status==="done"?"готово ✓":"ошибка"}`;
 $("#logb").innerHTML=`<pre style="background:none;border:0;color:inherit;max-height:none;padding:0;margin:0">${esc(t.log)}${t.error?"\n"+esc(t.error):""}</pre>`;
 $("#log").scrollTop=1e9;if(t.status==="running"){pollT=setTimeout(poll,2000)}else{watching=null;openEp(ep.id)}}
(async()=>{await loadEps();const t=await api("/api/task");if(t){watching=t.id;showLog();poll()}
 const first=document.querySelector(".ep");if(first)first.click()})();
</script>
</body></html>"""
