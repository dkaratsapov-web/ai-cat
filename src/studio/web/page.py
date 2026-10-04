"""HTML-страница панели (одним файлом, без внешних зависимостей)."""

PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>МяуРкетинг · Студия</title>
<link rel="icon" href="data:,">
<style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#16181d;--muted:#5b6170;--line:#e5e7eb;--accent:#ffd43b;--accent-ink:#1a1a1a;
--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--focus:#2563eb;--chip:#f1f3f5;--shadow:0 1px 2px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--card:#1a1d24;--ink:#eceef2;--muted:#9aa3b2;--line:#2a2f3a;
--chip:#232733;--shadow:none;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--focus:#93c5fd}}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink)}
header{display:flex;align-items:center;gap:12px;padding:14px 22px;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:5}
header .logo{font-weight:800;font-size:18px}header .logo b{background:var(--accent);color:var(--accent-ink);padding:2px 8px;border-radius:8px}
header .sp{flex:1}.budget{font-size:13px;color:var(--muted)}
.wrap{display:grid;grid-template-columns:280px minmax(0,1fr);min-height:calc(100vh - 58px)}
aside{border-right:1px solid var(--line);padding:16px;background:var(--card)}
aside>*{min-width:0}aside h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:6px 0 10px}
.ep{display:block;width:100%;text-align:left;font-weight:400;padding:10px 12px;border-radius:10px;cursor:pointer;margin-bottom:4px;border:1px solid transparent;background:none}
.ep:hover{background:var(--chip)}.ep.active{border-color:var(--accent);background:var(--chip)}
.ep .t{display:block;font-weight:600;font-size:14px}.ep .s{display:block;font-size:12px;color:var(--muted)}
.new{margin-top:16px;display:flex;flex-direction:column;gap:8px}
select{width:100%;max-width:100%;text-overflow:ellipsis}
select,textarea,input{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px}
main{padding:22px 26px 140px;max-width:1400px;min-width:0}
.sub{overflow-wrap:anywhere}
h1{margin:0 0 4px;font-size:24px}.sub{color:var(--muted);margin-bottom:16px}
.steps{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0 18px}
.step{padding:6px 12px;border-radius:999px;background:var(--chip);font-size:13px;color:var(--muted)}
.step.done{color:var(--ok)}.step.cur{background:var(--accent);color:var(--accent-ink);font-weight:700}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow);margin-bottom:18px}
.bar .info{flex:1;min-width:220px}.bar .info b{font-size:16px}
button{font:inherit;font-weight:600;border:0;border-radius:10px;padding:10px 16px;cursor:pointer;background:var(--chip);color:var(--ink)}
button.primary{background:var(--accent);color:var(--accent-ink)}button.danger{background:#fee2e2;color:#991b1b}
button:disabled{opacity:.5;cursor:not-allowed}button.sm{padding:6px 10px;font-size:13px;min-height:32px}
:focus-visible{outline:3px solid var(--focus);outline-offset:2px}button:focus-visible,.ep:focus-visible{box-shadow:0 0 0 5px var(--card)}
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.hint{font-size:12px;color:var(--muted)}
.warn{background:#fff7e6;color:#8a5300;border-radius:10px;padding:10px 14px;margin-bottom:14px;font-size:14px}
@media (prefers-color-scheme:dark){.warn{background:#3a2a10;color:#ffd58a}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.sc{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:var(--shadow);display:flex;flex-direction:column}
.thumb{aspect-ratio:9/16;background:#0d0f14;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative}
.thumb img,.thumb video{width:100%;height:100%;object-fit:cover}.thumb img.fit{object-fit:contain;background:#fff}
.thumb .card{color:#fff;padding:44px 18px 18px;font-size:14px;align-self:stretch;width:100%}
.thumb .card h4{margin:4px 0 12px;font-size:18px}.thumb .card li{margin:6px 0}
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
.final video{width:100%;aspect-ratio:9/16;max-height:80vh;border-radius:14px;background:#000}
pre{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;font-size:12.5px;max-height:420px;overflow:auto}
.log{position:fixed;left:0;right:0;bottom:0;background:#0f1117;color:#d6dae3;font:12.5px/1.45 ui-monospace,Consolas,monospace;max-height:34vh;overflow:auto;padding:10px 18px;border-top:3px solid var(--accent);display:none;z-index:9}
.log .h{font:600 13px system-ui;color:#fff;margin-bottom:6px;display:flex;gap:10px;align-items:center}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:20}
.modal .box{background:var(--card);border-radius:16px;padding:22px;max-width:460px;width:92%}
.modal h3{margin:0 0 8px}.modal .price{font-size:28px;font-weight:800;margin:10px 0}
.empty{color:var(--muted);padding:40px;text-align:center}
.covers img{height:220px;max-width:100%;object-fit:contain;border-radius:10px;margin-right:8px}
.err{background:#fee2e2;color:#991b1b;border-radius:10px;padding:10px 14px;margin-bottom:14px;font-size:14px}
@media (prefers-color-scheme:dark){.err{background:#3b1414;color:#fecaca}}
@media (max-width:900px){.wrap{grid-template-columns:minmax(0,1fr)}main{padding:18px 16px 140px}header{padding:12px 16px;flex-wrap:wrap}aside{border-right:0;border-bottom:1px solid var(--line)}.final{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><div class="logo"><b>Мяу</b>Ркетинг · Студия</div><div class="sp"></div><div class="budget" id="budget"></div></header>
<div class="wrap">
<aside>
  <nav aria-labelledby="eps-h"><h3 id="eps-h">Ролики</h3><div id="eps"></div></nav>
  <div class="new"><h3 id="new-h">Новый ролик</h3><label class="sr" for="tpl">Шаблон сценария</label><select id="tpl"></select><button onclick="newEp()">Создать из шаблона</button>
  <div class="meta">Новые сценарии по вашему заданию пишет Claude в чате — они появятся в этом списке.</div></div>
</aside>
<main id="main" tabindex="-1"><div class="empty">Загрузка…</div></main>
</div>
<section class="log" id="log" aria-label="Журнал операции"><div class="h"><span id="logt" role="status" aria-live="polite"></span><span class="sp" style="flex:1"></span><button class="sm" onclick="hideLog()">Скрыть журнал</button></div><div id="logb"></div></section>
<div class="modal" id="modal" onclick="if(event.target===this)closeModal()"><div class="box" role="dialog" aria-modal="true" aria-labelledby="mt" aria-describedby="md mp"><h3 id="mt"></h3><div id="md" class="meta"></div><div class="price" id="mp"></div>
<div class="row"><button class="primary" id="mok">Подтвердить оплату</button><button id="mcancel" onclick="closeModal()">Отмена</button></div></div></div>
<script>
const TOKEN="__TOKEN__";let cur=null,ep=null,pollT=null,watching=null;
const $=s=>document.querySelector(s);
const esc=s=>(s??"").toString().replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const usd=v=>"$"+(+v||0).toFixed(2);
async function api(u,body){const o=body?{method:"POST",headers:{"Content-Type":"application/json","X-Token":TOKEN},body:JSON.stringify(body)}:{};
 const r=await fetch(u,o);const j=await r.json();if(!r.ok||j&&j.error)throw new Error(j.error||r.status);return j}
async function loadEps(){const eps=await api("/api/episodes");$("#eps").innerHTML=eps.filter(e=>!e.mock).map(e=>
 `<button type="button" class="ep ${e.id===cur?"active":""}"${e.id===cur?' aria-current="true"':""} onclick="openEp('${e.id}')"><span class="t">${esc(e.title)}</span><span class="s">${esc(e.id.split("-").slice(0,2).join("-"))} · ${esc(e.status_ru)}</span></button>`).join("")||'<div class="meta">Пока нет роликов. Создайте ролик из шаблона ниже или попросите Claude написать сценарий.</div>';
 const t=await api("/api/templates");$("#tpl").innerHTML=t.map(x=>`<option value="${x.id}">${esc(x.title)} (${esc(x.type)})</option>`).join("")}
async function newEp(){try{const r=await api("/api/new",{template:$("#tpl").value});await loadEps();openEp(r.id)}catch(e){alert(e.message)}}
function showErr(m){$("#main").innerHTML=`<div class="err" role="alert">Не удалось загрузить данные: ${esc(m)}. Проверьте, что <code>studio web</code> запущен, и обновите страницу.</div>`}
async function openEp(id){cur=id;try{await loadEps();ep=await api("/api/episode/"+id);render()}catch(e){showErr(e.message)}}
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
 h+=`<ol class="steps" aria-label="Этапы производства" style="list-style:none;padding:0">${STEPS.map((x,i)=>`<li class="step ${i<si?"done":i===si?"cur":""}"${i===si?' aria-current="step"':""}>${i<si?'<span aria-hidden="true">✓ </span>':""}${x[1]}${i<si?'<span class="sr"> (готово)</span>':""}</li>`).join("")}</ol>`;
 h+=`<div class="bar"><div class="info"><b>${esc(e.status_ru)}</b><div class="meta">${esc(n.info)}</div></div>`+
   (n.btn?`<button class="primary" onclick="doAct('${n.act}',${!!n.paid},'${esc(n.price||"")}')">${esc(n.btn)}</button>`:"")+
   `<button onclick="doAct('refresh')" title="Проверить статус задач в Kling (бесплатно)">Обновить статус</button>${["voiced","generated","assembled","qa_failed","qa_passed"].includes(e.status)?`<button onclick="doAct('assemble')" title="Собрать ролик заново из готовых сцен (бесплатно)">Пересобрать</button>`:""}</div>`;
 if(e.disclaimer)h+=`<div class="warn">Дисклеймер: ${esc(e.disclaimer)}</div>`;
 if(e.warnings.length)h+=`<div class="warn">${e.warnings.map(esc).join("<br>")}</div>`;
 if(e.estimate.error)h+=`<div class="warn">Смета: ${esc(e.estimate.error)}</div>`;
 h+=`<div class="grid">${e.scenes.map(sceneCard).join("")}</div>`;
 if(e.final_video){h+=`<div class="final"><div><video controls playsinline preload="metadata" aria-label="Готовый ролик" src="/media/${e.id}/output/${encodeURIComponent(e.final_video)}?t=${Date.now()}"></video></div><div>`+
   (e.qa?`<h3>Техническая проверка</h3><pre>${esc(e.qa)}</pre>`:"")+`</div></div>`}
 if(e.publish){const p=e.publish;h+=`<h3>Публикация</h3><div class="covers">${e.covers.map((c,i)=>`<img src="/media/${e.id}/publish/${encodeURIComponent(c)}" alt="Обложка ${i+1}">`).join("")}</div>
   <pre>${esc((p.titles||[]).join("\n"))}\n\n${esc(p.description)}\n\n${esc(p.cta)}\n\n${esc((p.hashtags||[]).join(" "))}</pre><div class="meta">Файлы: projects/${esc(e.id)}/publish/. Публикуете вы сами.</div>`}
 $("#main").innerHTML=h}
const JOB_RU={succeeded:"сгенерировано",failed:"ошибка генерации",processing:"в работе",submitted:"отправлено",submitting:"отправка",unknown:"статус неизвестен"};
function sceneCard(s){let t;
 if(s.has_clip)t=`<video src="/media/${ep.id}/scenes/${encodeURIComponent(s.clip)}?t=${Date.now()}" muted loop playsinline controls preload="metadata" aria-label="Видео сцены ${s.id}"${s.reference_file?` poster="/ref/${s.reference_file}"`:""}></video>`;
 else if(s.local_image)t=`<img class="fit" src="/img/${ep.id}/${encodeURIComponent(s.local_image)}" alt="${esc(s.caption||"Скриншот сцены "+s.id)}">`;
 else if(s.reference_file)t=`<img src="/ref/${s.reference_file}" alt="Референс кота: ${esc(s.reference)}">`;
 else if(s.local_kind==="card"||s.local_kind==="chart")t=`<div class="card"><h4>${esc(s.title||"")}</h4><ul>${(s.bullets||[]).map(b=>`<li>${esc(b)}</li>`).join("")}</ul></div>`;
 else t=`<div class="card">${esc(s.visual)}</div>`;
 const est=s.estimate?`<span class="chip">${usd(s.estimate.usd)}${s.estimate.reused?" · уже оплачено":""}</span>`:"";
 const job=s.job?`<span class="chip ${s.job.status==="succeeded"?"ok":s.job.status==="failed"?"bad":"run"}">${esc(JOB_RU[s.job.status]||s.job.status)}</span>`:"";
 const err=s.job&&s.job.status==="failed"&&s.job.error?`<div class="meta" style="color:var(--bad)">${esc(s.job.error)}</div>`:"";
 const gen=s.paid&&["voiced","generated","assembled","qa_failed","qa_passed"].includes(ep.status)&&ep.approved?
   (s.has_clip?`<button class="sm" onclick="regen('${s.id}')" title="Новая платная генерация в Kling">Переделать сцену · ${s.estimate?usd(s.estimate.usd):"платно"}</button>`:`<button class="sm primary" onclick="genOne('${s.id}')" title="Платная генерация одной сцены в Kling">Сгенерировать эту · ${s.estimate?usd(s.estimate.usd):"платно"}</button>`):"";
 return `<div class="sc"><div class="thumb">${t}<span class="badge">${s.id} · ${s.start}с</span><span class="badge r ${s.paid?"paid":""}" title="${s.paid?"Видео генерируется в Kling — платно":"Кадр собирается локально — бесплатно"}">${s.paid?"Kling · платно":"бесплатно"}</span></div>
 <div class="body"><div class="meta">${esc(s.type_ru)}${s.reference?" · "+esc(s.reference):""} · ${s.duration} с</div>
 <label class="sr" for="vo-${s.id}">Текст озвучки сцены ${s.id}</label><textarea id="vo-${s.id}">${esc(s.voiceover)}</textarea>
 <div class="row"><button class="sm" onclick="saveVo('${s.id}')">Сохранить текст</button>${gen}</div>
 ${s.has_audio?`<audio controls preload="metadata" aria-label="Озвучка сцены ${s.id}" src="/media/${ep.id}/audio/${s.id}.wav?t=${Date.now()}"></audio>`:s.voiceover?'<div class="hint">Озвучки пока нет — появится после этапа «Озвучка».</div>':""}
 <div class="chips">${est}${job}${s.has_audio?`<span class="chip ok">озвучено · ${(+s.audio_duration||0).toFixed(1)} с</span>`:""}${s.has_clip?'<span class="chip ok">видео есть</span>':""}</div>${err}
 ${s.visual?`<div class="meta">Кадр: ${esc(s.visual)}</div>`:""}</div></div>`}
async function saveVo(id){const v=$("#vo-"+id).value;if(!confirm("Сохранить текст? Сценарий потребует повторного утверждения, сцену нужно будет переозвучить."))return;
 try{await api(`/api/episode/${ep.id}/action`,{action:"edit_scene",scene:id,voiceover:v});openEp(ep.id)}catch(e){alert(e.message)}}
function genOne(id){const s=ep.scenes.find(x=>x.id===id);ask(`Сгенерировать сцену ${id}`,`Тест одной сцены в Kling.`,usd(s.estimate?s.estimate.usd:0),()=>run("generate",{scenes:[id],confirm:true}))}
function regen(id){const s=ep.scenes.find(x=>x.id===id);ask(`Переделать сцену ${id}`,`Новая платная попытка. Прошлый вариант сохранится как .prev.`,usd(s.estimate?s.estimate.usd:0),()=>run("generate",{scenes:[id],regenerate:[id],confirm:true}))}
function doAct(act,paid,price){if(paid){ask(act==="voice"?"Озвучка":"Генерация сцен",act==="voice"?"Yandex SpeechKit, оплата по факту.":"Kling. Готовые сцены повторно не оплачиваются.",price,()=>run(act,{confirm:true}));return}
 run(act,{})}
let lastFocus=null;
function ask(t,d,p,fn){lastFocus=document.activeElement;$("#mt").textContent=t;$("#md").textContent=d;$("#mp").textContent=p;$("#modal").style.display="flex";$("#mok").onclick=()=>{closeModal();fn()};$("#mcancel").focus()}
function closeModal(){$("#modal").style.display="none";if(lastFocus&&document.contains(lastFocus))lastFocus.focus();lastFocus=null}
document.addEventListener("keydown",e=>{const m=$("#modal");if(m.style.display!=="flex")return;
 if(e.key==="Escape"){e.preventDefault();closeModal()}
 else if(e.key==="Tab"){const b=[$("#mok"),$("#mcancel")],i=b.indexOf(document.activeElement);e.preventDefault();b[(i+(e.shiftKey?-1:1)+b.length)%b.length].focus()}});
async function run(act,extra){try{const r=await api(`/api/episode/${ep.id}/action`,{action:act,...extra});
 if(r.task){watching=r.task;showLog();poll()}else openEp(ep.id)}catch(e){alert(e.message)}}
function showLog(){$("#log").style.display="block"}function hideLog(){$("#log").style.display="none"}
async function poll(){clearTimeout(pollT);if(!watching)return;let t;try{t=await api("/api/task/"+watching)}catch(e){$("#logt").textContent="Нет связи с панелью — повтор через 5 с";pollT=setTimeout(poll,5000);return}if(!t)return;
 $("#logt").textContent=`${t.title}: ${t.status==="running"?"выполняется…":t.status==="done"?"готово ✓":"ошибка"}`;
 $("#logb").innerHTML=`<pre style="background:none;border:0;color:inherit;max-height:none;padding:0;margin:0">${esc(t.log)}${t.error?"\n"+esc(t.error):""}</pre>`;
 $("#log").scrollTop=1e9;if(t.status==="running"){pollT=setTimeout(poll,2000)}else{watching=null;openEp(ep.id)}}
(async()=>{try{await loadEps();const t=await api("/api/task");if(t){watching=t.id;showLog();poll()}}catch(e){showErr(e.message);return}
 const first=document.querySelector(".ep");if(first)first.click();else $("#main").innerHTML='<div class="empty">Роликов пока нет. Создайте первый из шаблона слева.</div>'})();
</script>
</body></html>"""
