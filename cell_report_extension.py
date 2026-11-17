# EPANET-AI: дополнительные таблицы и локальная схема. Встраивается в ноутбук.
import csv as _csv
import html as _html
import json as _json
from pathlib import Path as _Path

def _report_extension(html_doc, cleaned_dir, geometry_path=None, chart_payload=None, facts_for_warning=None):
    """Встраивает полные сводки и схему INP в автономный HTML; не определяет CRS."""
    root = _Path(cleaned_dir)
    def read(name):
        p = root / name
        if not p.exists(): return None
        with p.open(encoding='utf-8-sig', newline='') as f:
            return list(_csv.DictReader(f))
    def number(x):
        try: return float(x)
        except (TypeError, ValueError): return None
    nodes, pipes, pumps = (read(x) for x in ('Узел_абоненты_сводка.csv', 'Труба_аналитика_сводка.csv', 'Насос_активные_сводка.csv'))
    def bad_node(r):
        m, p = number(r.get('margin_min_m')), number(r.get('free_pressure_min_m'))
        return (m is not None and m < 0) or (p is not None and p < 0)
    def bad_pipe(r):
        a, v = number(r.get('roughness_score_pts')), number(r.get('velocity_max_m_s'))
        return (a is not None and a < 80) or (v is not None and (v > 1.5 or v < .3))
    tables = {'nodes': None if nodes is None else [r for r in nodes if bad_node(r)],
              'pipes': None if pipes is None else [r for r in pipes if bad_pipe(r)],
              'pumps': pumps}
    # INP IDs сверяются по типу: c<INP-node>, e<INP-pipe>, p<INP-pump>.
    # Схема — в исходной системе координат, без неподтверждённого WGS84.
    geo = {'nodes': {}, 'links': [], 'file': None}
    # Совпадение только по уникальному точному ID или добавлению типового префикса.
    node_ids = {r.get('node_id') for r in (nodes or [])}
    pipe_ids = {r.get('pipe_id') for r in (pipes or [])}
    pump_ids = {r.get('pump_id') for r in (pumps or [])}
    def object_id(raw, prefix, known):
        candidates = [v for v in (raw, prefix + raw) if v in known]
        return candidates[0] if len(candidates) == 1 else (None if len(candidates) > 1 else (raw if raw.startswith(prefix) else prefix + raw))
    if geometry_path:
        raw = _Path(geometry_path).read_bytes()
        for encoding in ('utf-8-sig','cp1251'):
            try: text=raw.decode(encoding); break
            except UnicodeDecodeError: pass
        else: raise ValueError('Не удалось определить кодировку INP')
        section=''; verts={}; links=[]; duplicate_nodes=set()
        for line in text.splitlines():
            line=line.split(';',1)[0].strip()
            if line.startswith('[') and ']' in line: section=line[1:line.index(']')].upper(); continue
            a=line.split()
            if len(a)<3: continue
            try:
                if section=='COORDINATES':
                    key=object_id(a[0], 'c', node_ids)
                    if key is None: continue
                    if key in geo['nodes']: duplicate_nodes.add(key)
                    else: geo['nodes'][key]=[float(a[1]),float(a[2])]
                elif section=='VERTICES': verts.setdefault(a[0],[]).append([float(a[1]),float(a[2])])
                elif section in ('PIPES','PUMPS','VALVES'): links.append((section,a[0],a[1],a[2]))
            except ValueError: continue
        for key in duplicate_nodes: geo['nodes'].pop(key, None)
        if duplicate_nodes: print(f'⚠️ Неоднозначные координаты для {len(duplicate_nodes)} ID исключены со схемы.')
        for typ,ident,a,b in links:
            p1=geo['nodes'].get(object_id(a, 'c', node_ids)); p2=geo['nodes'].get(object_id(b, 'c', node_ids))
            if p1 and p2: geo['links'].append([object_id(ident, {'PIPES':'e','PUMPS':'p','VALVES':'v'}[typ], pipe_ids if typ=='PIPES' else pump_ids),[p1]+verts.get(ident,[])+[p2]])
        geo['file']=_Path(geometry_path).name
        matched=sum(r['node_id'] in geo['nodes'] for r in (nodes or []))
        print(f'Геометрия: совпало узлов {matched} из {len(nodes or [])}; проверьте соответствие моделей.')
    if facts_for_warning is not None:
        _pmin=number((facts_for_warning.get('nodes_summary') or {}).get('free_pressure_min_m'))
        if _pmin is not None and abs(_pmin) > 1000:
            warning=('<div role="alert" style="margin:20px 0;padding:16px;background:#fff7ed;border:1px solid #f97316;border-radius:10px;color:#7c2d12">'
                '<strong>Проверка качества расчёта:</strong> обнаружен экстремальный свободный напор '
                + _html.escape(str(_pmin)) + ' м. Причина не установлена; агрегаты дефицита и вакуума могут включать недостоверные значения. '
                'До выбора мероприятий проверьте расчёт и исходные значения. Порог 1000 м — служебный индикатор, не норматив.</div>')
            html_doc=html_doc.replace('<section class="ai-report-card">',warning+'<section class="ai-report-card">',1)
    payload=_json.dumps({'tables':tables,'geo':geo,'charts':chart_payload or {}},ensure_ascii=False,allow_nan=False).replace('<','\\u003c')
    insert='''<style>.extra {margin:24px 0;padding:22px;background:white;border:1px solid #d9e4ef;border-radius:14px}.extra button {padding:9px;margin:4px;border:1px solid #94a3b8;border-radius:8px;background:#eff6ff;cursor:pointer}.extra input {padding:9px}.extra-scroll {overflow:auto;max-height:420px}.extra table {border-collapse:collapse;font-size:12px}.extra td,.extra th {border:1px solid #e2e8f0;padding:6px;white-space:nowrap}.extra canvas {width:100%;height:480px;border:1px solid #cbd5e1;background:#f8fafc}.extra small {color:#475569}.object-link {background:transparent;border:0;border-bottom:1px dashed #0284c7;color:#0369a1;font:inherit;cursor:pointer;padding:0 2px}.object-link:hover,.object-link:focus {color:#7c3aed;text-decoration:underline}.extra .object-link {background:transparent;border:0;border-bottom:1px dashed #0284c7;padding:0 2px;margin:0}.map-controls {display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin:10px 0}#networkMap {touch-action:none;cursor:grab}#networkMap:active {cursor:grabbing}</style>
<section class="extra"><h2>Полные расчётные таблицы</h2><p>Состав: дефицит и вакуум; трубы с баллом &lt;80 или Vmax &lt;0,3 / &gt;1,5 м/с; все работающие насосы. Малый Vmax — индикатор, а не доказательство застоя. Отсутствующие данные не считаются нулём.</p><div id="extraButtons"></div><input id="extraSearch" placeholder="Поиск по строке"><div class="extra-scroll"><table id="extraTable"></table></div><button id="extraPrev">Назад</button><span id="extraPage"></span><button id="extraNext">Вперёд</button></section>
<section class="extra"><h2>Дополнительные графики расчётных данных</h2><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px"><div><h3>Распределение труб по скорости</h3><canvas id="velocityExtra" style="height:250px"></canvas></div><div><h3>Длина труб по диапазонам диаметра</h3><canvas id="diameterExtra" style="height:250px"></canvas></div></div><small>Скоростные категории рассчитаны по максимальной скорости за период; при отсутствии исходных рядов выводы ограничены. Диаметр — протяжённость, км.</small></section>
<section class="extra" id="mapSection"><h2>Схема сети</h2><p id="mapInfo" role="status" aria-live="polite"></p><small>Схема в исходных координатах модели, без географической подложки. Красным показаны объекты с расчётными отклонениями. Колесо мыши — масштаб, перетаскивание — перемещение.</small><div class="map-controls"><button type="button" id="mapPlus" title="Увеличить">＋</button><button type="button" id="mapMinus" title="Уменьшить">−</button><button type="button" id="mapReset">Вся сеть</button><input id="mapSearch" placeholder="ID объекта, например c123" aria-label="ID объекта"><button type="button" id="mapFind">Найти на схеме</button></div><canvas id="networkMap" aria-label="Интерактивная схема сети"></canvas></section>
<script>const EXTRA=__PAYLOAD__;let selected='nodes',page=0;const labels={nodes:'Проблемные узлы',pipes:'Проблемные трубы',pumps:'Работающие насосы'};const buttons=document.getElementById('extraButtons');
for(const k of Object.keys(labels)){let b=document.createElement('button');b.textContent='Посмотреть все: '+labels[k];b.onclick=()=>{selected=k;page=0;drawTable()};buttons.appendChild(b);let d=document.createElement('button');d.textContent='Скачать CSV: '+labels[k];d.onclick=()=>download(k);buttons.appendChild(d)}
function download(k){let rows=EXTRA.tables[k];if(rows===null){alert('Полная сводка отсутствует: повторите обработку CSV.');return}let keys=rows.length?Object.keys(rows[0]):[];let q=x=>'"'+String(x??'').replace(/"/g,'""')+'"';let txt='\\ufeff'+[keys,...rows.map(r=>keys.map(k=>r[k]))].map(r=>r.map(q).join(';')).join('\\r\\n');let url=URL.createObjectURL(new Blob([txt],{type:'text/csv;charset=utf-8'}));let a=document.createElement('a');a.href=url;a.download=k+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function drawTable(){let rows=EXTRA.tables[selected], table=document.getElementById('extraTable');table.replaceChildren();if(rows===null){document.getElementById('extraPage').textContent='Сводка отсутствует — запустите обработку заново';return}let term=document.getElementById('extraSearch').value.toLowerCase();let matches=rows.filter(r=>Object.values(r).some(x=>String(x??'').toLowerCase().includes(term)));let pages=Math.max(1,Math.ceil(matches.length/100));page=Math.min(page,pages-1);document.getElementById('extraPage').textContent=` ${page+1}/${pages} · найдено ${matches.length} `;if(!matches.length)return;let keys=Object.keys(matches[0]);let head=table.createTHead().insertRow();keys.forEach(k=>{let t=document.createElement('th');t.textContent=k;head.appendChild(t)});let body=table.createTBody();matches.slice(page*100,page*100+100).forEach(r=>{let tr=body.insertRow();keys.forEach(k=>{const td=tr.insertCell(),v=r[k]??'';if(['node_id','pipe_id','pump_id'].includes(k)&&v){const button=document.createElement('button');button.type='button';button.className='object-link';button.dataset.mapId=String(v);button.title='Показать на схеме';button.textContent=String(v);td.appendChild(button)}else td.textContent=v})})}
document.getElementById('extraSearch').oninput=()=>{page=0;drawTable()};document.getElementById('extraPrev').onclick=()=>{page=Math.max(0,page-1);drawTable()};document.getElementById('extraNext').onclick=()=>{page++;drawTable()};drawTable();
const networkCanvas = document.getElementById('networkMap');
const networkInfo = document.getElementById('mapInfo');
const networkSection = document.getElementById('mapSection');
const networkGeo = EXTRA.geo || {nodes:{},links:[]};
const linkLookup = new Map(), duplicateLinks = new Set();
for (const [id, points] of (networkGeo.links || [])) {
  if (!id) continue;
  if (linkLookup.has(id)) duplicateLinks.add(id);
  else linkLookup.set(id, points);
}
const networkView = {scale:1, tx:0, ty:0, baseScale:1, selected:null, selectedPoints:null, activeUntil:0, dragging:false};
const badPipes = new Set((EXTRA.tables.pipes || []).map(r=>r.pipe_id));
const mapCache = document.createElement('canvas');
function mapSize(){return {w:Math.max(1, networkCanvas.clientWidth),h:Math.max(1, networkCanvas.clientHeight)};}
function mapResize() {
  const {w,h}=mapSize(), dpr=window.devicePixelRatio || 1;
  networkCanvas.width=Math.round(w*dpr); networkCanvas.height=Math.round(h*dpr);
  mapCache.width=networkCanvas.width; mapCache.height=networkCanvas.height;
}
function screenPoint(p){return [p[0]*networkView.scale+networkView.tx, networkView.ty-p[1]*networkView.scale];}
function redrawBackground(){
  const {w,h}=mapSize(), dpr=window.devicePixelRatio||1, ctx=mapCache.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);ctx.lineWidth=1;
  for (const [id,points] of (networkGeo.links||[])) {
    if(!points || points.length<2)continue;ctx.beginPath();
    points.forEach((p,i)=>{let [x,y]=screenPoint(p);if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y)});
    ctx.strokeStyle=badPipes.has(id)?'#dc2626':'#aab9c7';ctx.lineWidth=badPipes.has(id)?1.6:0.8;ctx.stroke();
  }
  ctx.fillStyle='#e11d48';
  for(const r of (EXTRA.tables.nodes||[])) {const p=(networkGeo.nodes||{})[r.node_id];if(!p)continue;const [x,y]=screenPoint(p);if(x>=0&&x<=w&&y>=0&&y<=h){ctx.beginPath();ctx.arc(x,y,2.3,0,Math.PI*2);ctx.fill();}}
  redrawOverlay();
}
function redrawOverlay(){
  const dpr=window.devicePixelRatio||1,ctx=networkCanvas.getContext('2d');ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,networkCanvas.width,networkCanvas.height);ctx.drawImage(mapCache,0,0);
  if(!networkView.selectedPoints)return;
  ctx.setTransform(dpr,0,0,dpr,0,0);const points=networkView.selectedPoints;
  const blink=Date.now()<networkView.activeUntil && Math.floor(Date.now()/400)%2===0;
  ctx.strokeStyle=blink?'#facc15':'#7c3aed';ctx.fillStyle=blink?'#facc15':'#7c3aed';ctx.lineWidth=blink?7:5;
  if(points.length>1){ctx.beginPath();points.forEach((p,i)=>{let [x,y]=screenPoint(p);if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y)});ctx.stroke();}
  else {const [x,y]=screenPoint(points[0]);ctx.beginPath();ctx.arc(x,y,blink?9:6,0,Math.PI*2);ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();}
}
function mapFit(){
  const pts=Object.values(networkGeo.nodes||{}).filter(p=>p&&Number.isFinite(p[0])&&Number.isFinite(p[1]));
  if(!pts.length){networkInfo.textContent='Координаты не найдены; схема недоступна.';return;}
  mapResize();let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
  pts.forEach(([x,y])=>{xmin=Math.min(xmin,x);xmax=Math.max(xmax,x);ymin=Math.min(ymin,y);ymax=Math.max(ymax,y)});
  const {w,h}=mapSize(),scale=Math.min((w-40)/Math.max(xmax-xmin,1),(h-40)/Math.max(ymax-ymin,1));
  networkView.baseScale=scale;networkView.scale=scale;networkView.tx=w/2-(xmin+xmax)*scale/2;networkView.ty=h/2+(ymin+ymax)*scale/2;
  networkInfo.textContent=`Узлов с координатами: ${pts.length}; связей: ${(networkGeo.links||[]).length}. Выберите ID или воспользуйтесь поиском.`;redrawBackground();
}
function focusMapObject(id){
  id=String(id||'').trim();const nodes=networkGeo.nodes||{};
  networkSection.scrollIntoView({behavior:'smooth',block:'start'});
  if(duplicateLinks.has(id)){networkInfo.textContent=`${id}: неоднозначный ID в геометрии; объект не выделен.`;return false;}
  const node=Object.prototype.hasOwnProperty.call(nodes,id)?nodes[id]:null;
  const link=linkLookup.get(id);
  if(node&&link){networkInfo.textContent=`${id}: ID неоднозначен; объект не выделен.`;return false;}
  const pts=node?[node]:link;
  if(!pts||!pts.length){networkInfo.textContent=`${id}: нет однозначного совпадения или координат в INP.`;return false;}
  let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
  pts.forEach(([x,y])=>{xmin=Math.min(xmin,x);xmax=Math.max(xmax,x);ymin=Math.min(ymin,y);ymax=Math.max(ymax,y)});
  const {w,h}=mapSize(),spanX=xmax-xmin,spanY=ymax-ymin;
  let scale=networkView.baseScale*12;
  if(pts.length>1 && (spanX>0||spanY>0)) scale=Math.min((w*.55)/Math.max(spanX,1),(h*.55)/Math.max(spanY,1),networkView.baseScale*40);
  scale=Math.max(networkView.baseScale,scale);
  networkView.scale=scale;networkView.tx=w/2-(xmin+xmax)*scale/2;networkView.ty=h/2+(ymin+ymax)*scale/2;
  networkView.selected=id;networkView.selectedPoints=pts;networkView.activeUntil=Date.now()+6000;
  networkInfo.textContent=`Выбран объект ${id}. Подсветка мигает 6 секунд; точные координаты остаются в исходной системе модели.`;
  redrawBackground();return true;
}
function zoomMap(factor, x, y){
  const old=networkView.scale, next=Math.max(networkView.baseScale*.25,Math.min(networkView.baseScale*100,old*factor));
  const wx=(x-networkView.tx)/old, wy=(networkView.ty-y)/old;networkView.scale=next;networkView.tx=x-wx*next;networkView.ty=y+wy*next;redrawBackground();
}
networkCanvas.addEventListener('wheel',ev=>{ev.preventDefault();const rect=networkCanvas.getBoundingClientRect();zoomMap(ev.deltaY<0?1.25:.8,ev.clientX-rect.left,ev.clientY-rect.top)},{passive:false});
networkCanvas.addEventListener('pointerdown',ev=>{networkView.dragging=true;networkView.x=ev.clientX;networkView.y=ev.clientY;networkCanvas.setPointerCapture(ev.pointerId);});
networkCanvas.addEventListener('pointermove',ev=>{if(!networkView.dragging)return;networkView.tx+=ev.clientX-networkView.x;networkView.ty+=ev.clientY-networkView.y;networkView.x=ev.clientX;networkView.y=ev.clientY;redrawBackground();});
networkCanvas.addEventListener('pointerup',()=>{networkView.dragging=false});
networkCanvas.addEventListener('pointercancel',()=>{networkView.dragging=false});
document.getElementById('mapPlus').onclick=()=>{const {w,h}=mapSize();zoomMap(1.4,w/2,h/2)};
document.getElementById('mapMinus').onclick=()=>{const {w,h}=mapSize();zoomMap(1/1.4,w/2,h/2)};
document.getElementById('mapReset').onclick=()=>{networkView.selectedPoints=null;mapFit()};
document.getElementById('mapFind').onclick=()=>focusMapObject(document.getElementById('mapSearch').value);
document.getElementById('mapSearch').addEventListener('keydown',ev=>{if(ev.key==='Enter')focusMapObject(ev.target.value)});
document.addEventListener('click',ev=>{const b=ev.target.closest('[data-map-id]');if(b){ev.preventDefault();focusMapObject(b.dataset.mapId);}});
function makeIdButton(id){let b=document.createElement('button');b.type='button';b.className='object-link';b.dataset.mapId=id;b.title='Показать на схеме';b.textContent=id;return b;}
// Существующие top-таблицы и фрагменты отчёта с ID в <code>.
for(const code of document.querySelectorAll('.tables-section code, .ai-content code')){const id=code.textContent.trim();if(/^[cepv]\\d+$/i.test(id))code.replaceWith(makeIdButton(id));}
// Некоторые модели пишут ID обычным текстом — добавляем переход, не переписывая остальную разметку.
for(const container of document.querySelectorAll('.ai-content')){
  const walker=document.createTreeWalker(container,NodeFilter.SHOW_TEXT);const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);
  for(const node of nodes){if(node.parentElement && node.parentElement.closest('button,a,code,script,style'))continue;const text=node.nodeValue, re=/\\b[cepv]\\d+\\b/gi;if(!re.test(text))continue;re.lastIndex=0;let frag=document.createDocumentFragment(),last=0,m;while((m=re.exec(text))){frag.appendChild(document.createTextNode(text.slice(last,m.index)));frag.appendChild(makeIdButton(m[0]));last=m.index+m[0].length}frag.appendChild(document.createTextNode(text.slice(last)));node.replaceWith(frag);}
}
window.addEventListener('resize',mapFit);
requestAnimationFrame(mapFit);
setInterval(()=>{if(networkView.selectedPoints&&Date.now()<networkView.activeUntil+500)redrawOverlay()},400);

function extraBars(id, vals, units){let c=document.getElementById(id), ctx=c.getContext('2d');c.width=c.clientWidth*devicePixelRatio;c.height=c.clientHeight*devicePixelRatio;ctx.scale(devicePixelRatio,devicePixelRatio);let w=c.clientWidth,h=c.clientHeight,entries=Object.entries(vals||{}).filter(x=>typeof x[1]==='number'&&Number.isFinite(x[1]));ctx.font='12px sans-serif';if(!entries.length){ctx.fillStyle='#64748b';ctx.fillText('Нет данных',12,30);return}let max=Math.max(...entries.map(x=>x[1]),1),row=(h-20)/entries.length;entries.forEach(([k,v],i)=>{let y=i*row+10;ctx.fillStyle='#334155';ctx.fillText(k,8,y+14);ctx.fillStyle='#0284c7';ctx.fillRect(8,y+20,(w-100)*v/max,Math.max(3,Math.min(14,row-26)));ctx.fillStyle='#0f172a';ctx.fillText(String(v)+' '+units,Math.min(w-80,15+(w-100)*v/max),y+31)})}
requestAnimationFrame(()=>{extraBars('velocityExtra',EXTRA.charts.velocity_bins,'шт.');extraBars('diameterExtra',EXTRA.charts.diameter_km_bins,'км')});
</script>'''.replace('__PAYLOAD__',payload)
    return html_doc.replace('</body>',insert+'</body>')


import gc
import json
import time
import unicodedata
from pathlib import Path
from IPython.display import HTML, display

# ==============================================================================
# НАСТРОЙКИ ЗАПУСКА КНОПКИ «АНАЛИЗ ИИ»
# ==============================================================================
# Укажите имя города из data/ (например: "куляб", "тенино", "североморск", "лесное", "модель 10", "модель 11")
# либо полный путь к любой папке с необработанными CSV ПК «Аквабит»:
TARGET_CITY_OR_PATH = "модель семей"
# Необязательно: полный путь к текстовой INP-модели; TXT также допустим.
GEOMETRY_INP_PATH = None  # None: найти единственный *.inp в папке города
# GGUF: загрузка из Hugging Face только при первом запуске.
GGUF_REPO = "unsloth/Qwen3.5-4B-GGUF"
GGUF_FILENAME = "Qwen3.5-4B-Q4_K_M.gguf"
LLAMA_N_GPU_LAYERS = 32  # 0 = CPU; увеличивать только после проверки свободной VRAM
LLAMA_CONTEXT = 65536  # общий бюджет: промпт + ответ; требует дополнительной RAM/VRAM
LLAMA_BACKEND = "cuda"  # надёжный старт; "cuda" для экспериментальной загрузки на NVIDIA
LLAMA_SERVER_PATH = None  # иначе бинарник Windows скачивается с официального GitHub llama.cpp
INFERENCE_MAX_NEW_TOKENS = 24576  # максимум, не целевой размер отчёта

CUSTOM_ENGINEER_QUESTION = (
    "Сформируй полный инженерный отчёт ИИ-диагностики по расчётной модели ПК «Аквабит» по всем 5 разделам."
)

FAST_SYSTEM_PROMPT_V2 = (
    "Ты инженер-аналитик водопроводных сетей ПК «Аквабит». Пиши на русском в Markdown, без приветствий.\n"
    "Не описывай файлы, CSV, JSON, названия колонок, структуру выгрузки, очистку или ход обработки; "
    "допустимо кратко указать недостаточность или недостоверность расчётных данных, когда это влияет на вывод.\n"
    "Требуемое давление указано в метрах водяного столба. Не утверждай ошибку единиц или высот без доказательств. "
    "Высотная отметка узла — отметка местности, а не высота здания. Нулевые строки и транзитные узлы "
    "отфильтрованы только для аналитики, не удалены из гидравлического расчёта.\n"
    "Приводи только данные и ID из входа; не выдумывай значения, источник причины, норматив, КПД, расчёт экономии или аварии. "
    "Крайне неправдоподобное значение напора — предупреждение о качестве расчёта, а не доказанный вакуум, "
    "ошибка единиц или ошибка конкретного узла. Дефицит и вакуум, рассчитанные с такими значениями, "
    "считай предварительными до проверки; не обещай устранение всех таких узлов.\n"
    "Не называй объём воды в трубах пропускной способностью. Сумма подачи насосов не равна автоматически "
    "подаче в сеть и не позволяет определить потери: насосы могут стоять последовательно. "
    "Не считай отсутствие измерений нулём. Нулевая подача клапана сама по себе не доказывает его закрытие; "
    "реверс сам по себе не авария. Максимальная скорость менее 0.3 м/с лишь индикатор малой скорости. "
    "Одна скорость не доказывает гидроудар; QH без КПД не доказывает BEP, перегрузку или энергоэффективность. "
    "Относительный балл шероховатости: меньше 80 хуже, 80 нормальный, 100 хороший; балл не подтверждает физический износ.\n"
    "Структура строго: ## 1. Водный баланс; ## 2. Напоры; ## 3. Трубопроводы; "
    "## 4. Насосы и арматура; ## 5. Приоритетные инженерные мероприятия. "
    "Разделы 1–4 кратко: факт, значение, достоверность и при необходимости гипотеза. "
    "Раздел 5 — наиболее подробный и главный: отдельные приоритетные мероприятия по подтверждённым проблемам "
    "и условные варианты для неподтверждённых. Для каждого: цель, конкретные объекты только если ID имеются, "
    "обоснование фактами, что и как проверить, последовательность действий в модели, критерий проверки результата, "
    "ограничения и что нельзя утверждать без измерений. Укажи очередность, зависимости, сравнение сценариев "
    "и риски вмешательства. Не назначай замену труб, установку клапанов или реконструкцию как доказанное решение "
    "до обследования и проверочного расчёта. Не придумывай пороги, срок, стоимость, экономию или масштаб. "
    "Излагай предметно и понятно, без повторов и описания входных данных."
)


def _format_digest_for_infer(facts: dict, charts: dict) -> str:
    """Весь подготовленный паспорт и выбранные наборы данных графиков; не весь CSV R0..R1439."""
    chart_keys = (
        'hours_labels', 'diurnal_demand_m3_h', 'diurnal_pump_flow_m3_h',
        'diurnal_valve_flow_m3_h', 'diurnal_free_pressure_percentiles_m',
        'pressure_bins', 'velocity_bins', 'roughness_km_bins', 'diameter_km_bins',
        'demand_schedules_24h', 'qh_curves')
    prepared_charts = {key: charts.get(key) for key in chart_keys}
    _minimum = (facts.get('nodes_summary') or {}).get('free_pressure_min_m')
    try: _extreme_pressure = _minimum is not None and abs(float(_minimum)) > 1000
    except (TypeError, ValueError): _extreme_pressure = False
    _data_caution = ("ПРЕДУПРЕЖДЕНИЕ: экстремальный свободный напор; причина неизвестна. "
        "Агрегаты дефицита и вакуума предварительны и могут включать недостоверные точки. "
        "Порог 1000 м — служебная проверка, не норматив.\n" if _extreme_pressure else "")
    return (
        _data_caution + "РАСЧЁТНЫЕ ФАКТЫ JSON:\n" + json.dumps(facts, ensure_ascii=False, default=str, separators=(',', ':')) +
        "\nДАННЫЕ ГРАФИКОВ JSON:\n" + json.dumps(prepared_charts, ensure_ascii=False, default=str, separators=(',', ':')) +
        "\nДанные графиков — расчётные агрегаты, а не изображения или измерения. "
        "Это не полные исходные CSV: таблицы объектов в паспорте могут содержать только топ выборку, "
        "а payload QH и графиков ограничен при подготовке данных. Не считай их полным перечнем. "
        "В некоторых QH curve_points — интерполяция или предположенная парабола, а не паспортные измерения. "
        "Пустое или null означает нет данных. Не определяй BEP и электрическую энергию без данных о КПД. "
        "Сумма подачи насосов не обязательно равна отпуску из-за возможной повторной перекачки. "
        "Реверс не обязательно авария, низкая максимальная скорость не доказывает застой. "
        "Причины, кавитация и гидроудар — только гипотезы до проверки."
    )


# GGUF и llama-server изолированы от обучения/старых адаптеров.
def _download_llama_server(backend):
    import os, sys, zipfile, requests
    if LLAMA_SERVER_PATH:
        exe = Path(LLAMA_SERVER_PATH)
        if not exe.is_file(): raise FileNotFoundError(f"llama-server не найден: {exe}")
        return exe
    if sys.platform != 'win32':
        raise RuntimeError("Укажите LLAMA_SERVER_PATH для установленного llama-server этой ОС")
    target = MODELS_DIR / 'llama_cpp' / backend
    exe = target / 'llama-server.exe'
    if exe.exists(): return exe
    target.mkdir(parents=True, exist_ok=True)
    api = 'https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10'
    response = requests.get(api, timeout=45); response.raise_for_status()
    releases = response.json()
    match = None
    for release in releases:
        assets=release.get('assets', [])
        suffix = '-bin-win-cuda-12.4-x64.zip' if backend == 'cuda' else '-bin-win-cpu-x64.zip'
        # cudart-llama-bin-win-cuda-12.4-x64.zip тоже имеет такой суффикс,
        # но содержит только библиотеки CUDA, а не llama-server.exe.
        # Поэтому выбираем именно архив llama-bNNNN-bin-..., а не cudart.
        binary = next((a for a in assets if a['name'].startswith('llama-') and a['name'].endswith(suffix)), None)
        runtime = next((a for a in assets if a['name'] == 'cudart-llama-bin-win-cuda-12.4-x64.zip'), None) if backend == 'cuda' else None
        if binary and (backend != 'cuda' or runtime):
            match = (binary, runtime)
            print(f"llama.cpp release {release.get('tag_name', '?')}: {binary['name']}")
            break
    if match is None: raise RuntimeError('Не найден совместимый архив llama.cpp на GitHub; задайте LLAMA_SERVER_PATH')
    for asset in [a for a in match if a is not None]:
        url=asset['browser_download_url']
        if not url.startswith('https://github.com/ggml-org/llama.cpp/releases/download/'):
            raise ValueError('Неожиданный адрес архива llama.cpp')
        archive=target / asset['name']
        print(f"Скачивание {asset['name']} ({asset['size']/1048576:.0f} МБ)...")
        with requests.get(url, stream=True, timeout=(30,300)) as r:
            r.raise_for_status()
            with archive.open('wb') as out:
                for part in r.iter_content(1024*1024):
                    if part: out.write(part)
        with zipfile.ZipFile(archive) as z:
            # Проверяем ПЕРЕД извлечением: если серверного exe нет, сообщаем имя
            # реального архива и не подменяем его библиотеками CUDA.
            if asset is match[0] and not any(
                member.filename.replace('\\', '/').split('/')[-1].lower() == 'llama-server.exe'
                for member in z.infolist() if not member.is_dir()
            ):
                raise RuntimeError(f"Архив {asset['name']} не содержит llama-server.exe; выберите другую сборку через LLAMA_SERVER_PATH")
            # Не доверяем путям из архива, не извлекаем во вложенные каталоги.
            for member in z.infolist():
                name = Path(member.filename.replace('\\','/')).name
                if not member.is_dir() and name and name.lower().endswith(('.exe','.dll')):
                    with z.open(member) as src, (target/name).open('wb') as out:
                        import shutil; shutil.copyfileobj(src,out)
        archive.unlink()
    if not exe.exists(): raise FileNotFoundError(f"В архиве отсутствует {exe.name}")
    return exe


def _stream_local_gguf(messages):
    import os, socket, subprocess, tempfile, time, requests
    from huggingface_hub import hf_hub_download
    backend = LLAMA_BACKEND.lower()
    if backend not in ('cpu','cuda'): raise ValueError("LLAMA_BACKEND: 'cpu' или 'cuda'")
    exe = _download_llama_server(backend)
    print(f"Модель {GGUF_REPO}/{GGUF_FILENAME}: при первом запуске будет скачано несколько ГБ.")
    model_file=hf_hub_download(repo_id=GGUF_REPO, filename=GGUF_FILENAME,
        local_dir=str(MODELS_DIR / 'gguf' / GGUF_REPO.replace('/','--')))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    args=[str(exe), '-m', str(model_file), '--host','127.0.0.1','--port',str(port),
          '-c',str(LLAMA_CONTEXT), '-np','1', '-ngl',str(LLAMA_N_GPU_LAYERS if backend=='cuda' else 0), '--no-kv-offload', '--no-context-shift', '--jinja']
    chunks=[]; finish=None; count=None; start=time.perf_counter()
    # Сервер завершается даже при ошибке, чтобы не оставлять модель в памяти.
    with tempfile.TemporaryFile(mode='w+t',encoding='utf-8') as log:
        proc=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+300
            while time.monotonic()<deadline:
                if proc.poll() is not None:
                    log.seek(0); raise RuntimeError('llama-server завершился:\n'+log.read()[-6000:])
                try:
                    r=requests.get(base+'/health',timeout=2)
                    if r.status_code==200: break
                except requests.RequestException: pass
                time.sleep(1)
            else: raise TimeoutError('llama-server не запустился за 300 сек')
            # Токенизируем полный чат с шаблоном модели, не обрезая промпт.
            available = LLAMA_CONTEXT - INFERENCE_MAX_NEW_TOKENS - 256
            if available <= 0:
                raise ValueError('LLAMA_CONTEXT слишком мал для выбранного лимита ответа и запаса.')
            req = requests.post(base+'/apply-template', json={'messages': messages,
                'chat_template_kwargs': {'enable_thinking': False}}, timeout=60)
            if not req.ok:
                raise RuntimeError(f'Ошибка /apply-template HTTP {req.status_code}: {req.text[:1200]}')
            rendered = req.json().get('prompt')
            if not isinstance(rendered, str): raise RuntimeError('llama-server не вернул шаблон промпта')
            tok = requests.post(base+'/tokenize', json={'content':rendered, 'add_special':True}, timeout=60)
            if not tok.ok: raise RuntimeError(f'Ошибка /tokenize HTTP {tok.status_code}: {tok.text[:1200]}')
            token_ids = tok.json().get('tokens')
            if not isinstance(token_ids,list): raise RuntimeError('llama-server не вернул список токенов')
            n_prompt=len(token_ids)
            print(f'Контекст: полный подготовленный промпт {n_prompt} токенов; максимум входа {available}, резерв ответа {INFERENCE_MAX_NEW_TOKENS}.')
            if n_prompt > available:
                raise ValueError(f'Промпт не помещается без сокращения: {n_prompt} > {available} токенов. Увеличьте LLAMA_CONTEXT или уменьшите INFERENCE_MAX_NEW_TOKENS; потребуется больше памяти.')
            payload={'model':'local','messages':messages,'stream':True,'max_tokens':INFERENCE_MAX_NEW_TOKENS,
                'temperature':0.7,'top_p':0.8,'chat_template_kwargs':{'enable_thinking':False},
                'stream_options':{'include_usage':True}}
            print('\n🤖 Генерация отчёта Qwen3.5-4B:\n'+'='*70)
            with requests.post(base+'/v1/chat/completions',json=payload,stream=True,timeout=(15,900)) as r:
                if not r.ok: raise RuntimeError(f"llama-server HTTP {r.status_code}: {r.text[:3000]}")
                r.encoding = 'utf-8'  # SSE всегда UTF-8, даже без charset в заголовках
                for line in r.iter_lines(decode_unicode=True):
                    if not line or not line.startswith('data:'): continue
                    data=line[5:].strip()
                    if data=='[DONE]': break
                    event=json.loads(data)
                    if event.get('error'): raise RuntimeError(str(event['error']))
                    usage=event.get('usage') or {}
                    if usage.get('completion_tokens') is not None: count=usage['completion_tokens']
                    for choice in event.get('choices',[]):
                        piece=(choice.get('delta') or {}).get('content') or ''
                        if piece: chunks.append(piece);print(piece,end='',flush=True)
                        if choice.get('finish_reason'): finish=choice['finish_reason']
            if finish is None:
                raise RuntimeError('Поток завершился без finish_reason: отчёт может быть неполным')
            return ''.join(chunks), finish, count, round(time.perf_counter()-start,2)
        finally:
            proc.terminate()
            try: proc.wait(timeout=10)
            except subprocess.TimeoutExpired: proc.kill();proc.wait()

def run_aquabit_ai_analysis_live(target_city_or_path: str | Path, question: str) -> Path:
    t_total_start = time.perf_counter()
    cand_path = Path(target_city_or_path)
    cname_norm = unicodedata.normalize("NFC", str(target_city_or_path)).strip()

    if cand_path.exists() and cand_path.is_dir():
        print(f"⚙️ [1/3] Сверхбыстрая обработка папки CSV «{cand_path.name}»...")
        facts, chart_payload = process_aquabit_city_fast(cand_path, cleaned_root_dir=DATA_CLEANED_DIR, top_k=10)
    elif cname_norm in ALL_CITY_FACTS and cname_norm in ALL_CITY_CHARTS:
        facts, chart_payload = ALL_CITY_FACTS[cname_norm], ALL_CITY_CHARTS[cname_norm]
        print(f"⚡ [1/3] Подгружен паспорт и графики города «{cname_norm}» (обработка CSV заняла {facts.get('processing_time_sec', 0)} сек)")
    elif (RAW_DATA_DIR / cname_norm).exists():
        print(f"⚙️ [1/3] Сверхбыстрая обработка сырой папки «{RAW_DATA_DIR / cname_norm}»...")
        facts, chart_payload = process_aquabit_city_fast(RAW_DATA_DIR / cname_norm, cleaned_root_dir=DATA_CLEANED_DIR, top_k=10)
    elif ALL_CITY_FACTS:
        raise FileNotFoundError(f"Модель {target_city_or_path!r} не найдена; доступно: {list(ALL_CITY_FACTS)}")
    else:
        raise FileNotFoundError(f"Не найдены CSV-данные ни в {RAW_DATA_DIR.resolve()}, ни по пути {target_city_or_path}")

    cname = facts["city_name"]
    digest_str = _format_digest_for_infer(facts, chart_payload)
    user_prompt = f"Запрос анализа ПК «Аквабит»: {question}\n\nСводка расчёта Аквабит:\n{digest_str}"
    messages = [
        {"role": "system", "content": build_ontology_ai_system_prompt(load_hydraulic_ontology(RAW_DATA_DIR / "aquabit_hydraulic_ontology_v3.json"))},
        {"role": "user", "content": user_prompt},
    ]
    raw_full_text, _finish_reason, _out_count, gen_sec = _stream_local_gguf(messages)
    _token_limit_hit = _finish_reason != "stop"
    if _token_limit_hit:
        print("\n⚠️ Достигнут лимит вывода или контекста; отчёт может быть неполным.")
    model_label = GGUF_REPO + " / " + GGUF_FILENAME

    clean_ai_markdown = sanitize_pure_facts_response(raw_full_text)
    # Проверка структуры и повторов: при сбое не выдаём машинный текст за ИИ-вывод.
    import re
    _sections = set(re.findall(r"(?m)^\s*#{1,3}\s*([1-5])\.", clean_ai_markdown))
    _words = re.findall(r"[\wА-Яа-яЁё]+", clean_ai_markdown.lower())
    _grams = [tuple(_words[i:i+12]) for i in range(max(0, len(_words)-11))]
    _loop = len(_grams) > 20 and len(set(_grams)) < len(_grams)*0.8
    if len(_sections) < 2 or _loop or _token_limit_hit or raw_full_text.lstrip().startswith("{"):
        print("\n⚠️ Ответ может быть неполным, повторяющимся или неструктурированным. Он сохранён как черновик; требуется проверка.")
        clean_ai_markdown = ("**Внимание: черновик не прошёл автоматическую проверку структуры или завершения.**\n\n" + clean_ai_markdown)
    from IPython.display import Markdown
    display(Markdown(clean_ai_markdown))

    out_tokens_cnt = _out_count
    tok_per_sec = round(out_tokens_cnt / max(gen_sec, 0.01), 1) if out_tokens_cnt is not None else None
    print("\n" + "=" * 88)
    print(f"⚡ Генерация завершена: {out_tokens_cnt} токенов за {gen_sec} сек ({tok_per_sec} ток/сек)")

    out_html_path = REPORTS_DIR / f"EPANET_AI_Report_{cname}.html"
    _md_path = REPORTS_DIR / f"EPANET_AI_Report_{cname}.md"
    _md_path.write_text(clean_ai_markdown, encoding="utf-8")
    (REPORTS_DIR / f"EPANET_AI_Raw_{cname}.md").write_text(raw_full_text, encoding="utf-8")
    print(f"Markdown и исходный ответ сохранены: {_md_path}")
    
    # Пересчет графиков через Matplotlib Engine и извлечение выбранных ИИ дашбордов
    from aquabit_ai.hydraulic_matplotlib import render_all_recalculated_charts, assemble_adaptive_dashboard_html
    from aquabit_ai.ontology_engine import extract_ai_dashboards_and_content, load_hydraulic_ontology

    facts["chart_payload"] = chart_payload
    all_mpl_plots = render_all_recalculated_charts(facts)
    clean_ai_markdown, parsed_dashboards = extract_ai_dashboards_and_content(clean_ai_markdown)
    chosen_dashboards = [d["id"] for d in parsed_dashboards.get("dashboards", [])]
    print(f"\n💡 ИИ выбрал дашборды для отображения: {chosen_dashboards}")

    # Отображаем приоритетный график прямо в выводе Jupyter Notebook
    top_chart_id = chosen_dashboards[0] if chosen_dashboards else "pump_qh"
    if top_chart_id in all_mpl_plots:
        print(f"📊 Превью приоритетного дашборда ({all_mpl_plots[top_chart_id]['title']}):")
        display(HTML(f'<img src="{all_mpl_plots[top_chart_id]["b64"]}" style="max-width:85%;border-radius:8px;border:1px solid #cbd5e1;box-shadow:0 4px 6px rgba(0,0,0,0.05);"/>'))

    render_standalone_html_report(
        facts=facts,
        chart_payload=chart_payload,
        ai_markdown=clean_ai_markdown,
        output_html_path=out_html_path,
        generation_meta={
            "model_name": f"{model_label} ({tok_per_sec} tok/s)",
            "inference_sec": gen_sec,
        },
    )
    # Дополнение встраивается в HTML без интернета. Старые паспорта требуют повторной обработки.
    _summary_dir = DATA_CLEANED_DIR / cname
    _city_dir = cand_path if cand_path.is_dir() else RAW_DATA_DIR / cname
    _geometry = Path(GEOMETRY_INP_PATH) if GEOMETRY_INP_PATH else None
    if _geometry is None and _city_dir.is_dir():
        _candidates = sorted([p for p in _city_dir.iterdir() if p.is_file() and p.suffix.lower() == '.inp'])
        if len(_candidates) == 1:
            _geometry = _candidates[0]
        elif len(_candidates) > 1:
            raise ValueError(f"Найдено несколько INP для {cname}: {_candidates}; задайте GEOMETRY_INP_PATH явно")
        else:
            print(f"ℹ️ INP не найден в {_city_dir}; схема не построена")
    _charts_for_extra = dict(chart_payload)
    if not facts.get('pipes_summary', {}).get('flow_timeseries_available'):
        _charts_for_extra['velocity_bins'] = None
    _full_html = _report_extension(out_html_path.read_text(encoding="utf-8"), _summary_dir, _geometry, _charts_for_extra, facts)
    out_html_path.write_text(_full_html, encoding="utf-8")
    total_e2e = round(time.perf_counter() - t_total_start, 2)
    print(f"📊 [3/3] Готов интерактивный HTML-отчёт с графиками Кривой QH: {out_html_path.resolve()}")
    print(f"🏁 Полное время цикла «Анализ ИИ»: {total_e2e} сек!")
    display(HTML(f'<div style="padding:12px 16px;background:#ecfdf5;border:1px solid #10b981;border-radius:10px;margin-top:8px">'
                 f'✅ <b>Отчёт сохранён:</b> <code>{out_html_path.resolve()}</code> ({out_html_path.stat().st_size / 1024:.1f} КБ) — '
                 f'откройте его в любом браузере для интерактивного просмотра графиков <b>Кривая QH</b> и печати в PDF!</div>'))
    return out_html_path


REPORT_HTML_FILE = run_aquabit_ai_analysis_live(TARGET_CITY_OR_PATH, CUSTOM_ENGINEER_QUESTION)
