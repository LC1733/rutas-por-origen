/* Public-source snapshots. No credentials are sent to the browser. */
(() => {
  'use strict';
  const EXPECTED = ['dicapi','acp-gatun','acp-advisories','dhn','enfen','noaa'];
  const PUBLIC_DATA = 'https://raw.githubusercontent.com/LC1733/rutas-por-origen/main/data/live.json';
  const localPreview = ['127.0.0.1','localhost'].includes(location.hostname);
  const endpoint = localPreview ? 'data/live.json' : PUBLIC_DATA;
  const state = {snapshot:window.MARTILINE_LIVE_INITIAL || null, transport:'initial', error:null, receivedAt:null, busy:false, portFilter:'Todas'};
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const norm = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  const dateFormatter = new Intl.DateTimeFormat('es-PE',{day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'});
  const clockFormatter = new Intl.DateTimeFormat('es-PE',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'America/Lima'});
  const validTime = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
  const time = value => validTime(value) ? clockFormatter.format(new Date(value))+' PE' : 'Sin fecha verificable';
  const date = value => validTime(value) ? dateFormatter.format(new Date(/^\d{4}-\d{2}-\d{2}$/.test(value)?value+'T12:00:00Z':value)) : 'Sin fecha explícita';
  const hours = value => validTime(value) ? (Date.now()-Date.parse(value))/3600000 : Infinity;
  const source = id => state.snapshot?.sources?.[id] || null;
  const pill = (label,tone='gray') => '<span class="pill '+tone+'">'+esc(label)+'</span>';
  function safeUrl(value) {
    try {const u=new URL(value);if(u.protocol!=='https:')return null;return u.href;} catch(_){return null;}
  }
  function link(label,url,className='text-button') {
    const href=safeUrl(url);
    return href?'<a class="'+className+'" href="'+esc(href)+'" target="_blank" rel="noopener noreferrer">'+esc(label)+' ↗</a>':'<span class="muted">Enlace no disponible</span>';
  }
  function health(s) {
    if(!s?.lastSuccessAt)return {label:'Sin datos comprobados',tone:'red'};
    if(s.status==='error')return {label:'Falló la última consulta',tone:'red'};
    if(hours(s.lastSuccessAt)>2)return {label:'Sin renovación reciente',tone:'amber'};
    if(s.status==='partial')return {label:'Consulta parcial',tone:'amber'};
    return {label:'Consulta correcta',tone:'green'};
  }
  function stamp(id) {
    const s=source(id),h=health(s);
    return '<div class="live-source-stamp">'+pill(h.label,h.tone)+'<span>Último éxito: '+esc(time(s?.lastSuccessAt))+'</span></div>';
  }
  function emit(){window.dispatchEvent(new CustomEvent('martiline:live'));}
  function validSnapshot(x) {
    return !!x && x.schemaVersion===1 && validTime(x.generatedAt) && Date.parse(x.generatedAt)<=Date.now()+5*60*1000 && x.sources && !Array.isArray(x.sources) && EXPECTED.every(id=>x.sources[id] && x.sources[id].id===id && ['ok','partial','error'].includes(x.sources[id].status));
  }
  async function refresh() {
    if(state.busy)return;
    state.busy=true;state.error=null;emit();
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),18000);
    try {
      const url=new URL(endpoint,location.href);url.searchParams.set('v',String(Math.floor(Date.now()/60000)));
      const response=await fetch(url.href,{cache:'no-store',credentials:'omit',signal:controller.signal});
      if(!response.ok)throw new Error('HTTP '+response.status);
      const body=await response.text();
      if(body.length>2000000)throw new Error('Respuesta demasiado grande');
      const result=JSON.parse(body);
      if(!validSnapshot(result))throw new Error('Formato o fecha de publicación no válidos');
      if(state.snapshot?.generatedAt && Date.parse(result.generatedAt)<Date.parse(state.snapshot.generatedAt))throw new Error('El servidor devolvió una publicación anterior a la copia disponible');
      state.snapshot=result;state.transport='remote';state.receivedAt=new Date().toISOString();
    } catch(error) {
      state.transport='error';state.error=error.name==='AbortError'?'Tiempo de espera agotado':String(error.message||'Error de conexión').slice(0,180);
    } finally {clearTimeout(timeout);state.busy=false;emit();}
  }
  function banner(compact=false) {
    const snap=state.snapshot;
    let tone='green',title='Publicación de fuentes recibida',detail='El servicio consulta las fuentes cada hora. Esta pantalla comprueba nuevas publicaciones cada 5 minutos.';
    if(state.busy && state.transport==='initial'){tone='gray';title='Comprobando la última publicación';detail='La copia incluida conserva su fecha. Se está consultando la publicación disponible.';}
    else if(state.transport==='error'){tone='amber';title='No se pudo comprobar la publicación';detail='Se conserva la última copia disponible. '+state.error+'.';}
    else if(state.transport==='initial'){tone='gray';title='Copia incluida en el archivo';detail='Todavía no se ha comprobado si existe una publicación más reciente.';}
    else if(hours(snap?.generatedAt)>2){tone='red';title='La publicación lleva más de 2 horas sin renovarse';detail='Verifica la ejecución y las fuentes antes de usar estos datos.';}
    const entries=EXPECTED.map(source).filter(Boolean);
    const ok=entries.filter(s=>s.status==='ok'&&hours(s.lastSuccessAt)<=2).length;
    const partial=entries.filter(s=>s.status==='partial'&&hours(s.lastSuccessAt)<=2).length;
    if(state.transport==='remote' && hours(snap?.generatedAt)<=2 && ok<6){tone=ok+partial===0?'red':'amber';title='Publicación recibida con observaciones';detail='Revisa las fuentes con error, datos parciales o diferencias entre registros. Se conservan las fechas de los últimos datos disponibles.';}
    const generated=validTime(snap?.generatedAt)?time(snap.generatedAt):'No disponible';
    return '<section class="live-banner '+tone+'" aria-label="Actualización de fuentes"><div><strong>'+esc(title)+'</strong><p>'+esc(detail)+'</p><div class="live-banner-meta"><span>Publicación: '+esc(generated)+'</span><span>'+ok+' correctas'+(partial?' · '+partial+(partial===1?' parcial':' parciales'):'')+' / 6 fuentes</span><span>Horario mostrado: Perú (UTC−5)</span></div></div><div class="live-banner-actions"><button class="button secondary" data-action="refresh-live" '+(state.busy?'disabled':'')+'>'+esc(state.busy?'Comprobando…':'Recargar datos')+'</button>'+(!compact?link('Ver ejecución',snap?.runUrl):'<button class="text-button" data-view="monitoreo">Ver fuentes y estados →</button>')+'</div></section>';
  }
  function number(value){return typeof value==='number'&&Number.isFinite(value)?new Intl.NumberFormat('es-CL',{minimumFractionDigits:1,maximumFractionDigits:1}).format(value):null;}
  function kpis(){
    const gatun=source('acp-gatun'),advisory=source('acp-advisories'),ports=source('dicapi');
    const draft=number(gatun?.data?.neopanamaxDraftFeet);
    const latest=advisory?.data?.latest;
    return [
      {title:'Calado Neopanamax',value:draft||'Sin dato',unit:draft?'pies':'',detail:'Documento ACP: '+date(gatun?.data?.documentDate||gatun?.sourceDate),foot:stamp('acp-gatun'),icon:'ship'},
      {title:'Avisos del Canal',value:latest?.number||'Consultar',unit:'',detail:latest?.publishedAt?'Publicación: '+date(latest.publishedAt):'Cupos sujetos al aviso aplicable',foot:stamp('acp-advisories'),icon:'calendar',pending:true},
      {title:'DICAPI · Callao y Chancay',value:Array.isArray(ports?.data?.ports)?ports.data.ports.length:'Sin datos',unit:Array.isArray(ports?.data?.ports)?'instalaciones':'',detail:'Estados separados por instalación',foot:stamp('dicapi'),icon:'anchor',pending:!ports?.data?.ports}
    ];
  }
  function latestMarkup(id,label){
    const s=source(id),latest=s?.data?.latest,h=health(s);
    let expired='';
    if(latest?.validUntil && validTime(latest.validUntil) && Date.now()>Date.parse(latest.validUntil+'T23:59:59-05:00'))expired=pill('Vigencia terminada','red');
    return '<div class="pulse-item"><div class="pulse-top"><strong>'+esc(label)+'</strong>'+pill(h.label,h.tone)+'</div><p>'+esc(latest?.title||'No hay una publicación comprobada disponible.')+'</p><div class="live-document-date">Publicado: '+esc(date(latest?.publishedAt||s?.sourceDate))+'</div>'+(latest?.validFrom||latest?.validUntil?'<p>Vigencia declarada: '+esc(date(latest.validFrom))+' a '+esc(date(latest.validUntil))+' '+expired+'</p>':'')+link('Abrir publicación',latest?.url||s?.url)+'</div>';
  }
  function focus(){return '<section class="panel pulse-panel"><div class="section-heading"><h2>Publicaciones de seguimiento</h2></div>'+latestMarkup('enfen','ENFEN · Niño costero')+latestMarkup('dhn','DHN · Oleaje')+latestMarkup('noaa','NOAA · ENSO')+'</section>';}
  function sourceCard(id){
    const s=source(id),h=health(s),d=s?.data,latest=d?.latest;
    let body='';
    if(id==='dicapi'){
      const rows=Array.isArray(d?.ports)?d.ports:[];
      const dif=rows.filter(p=>p.discrepancy).length;
      body='<p>'+rows.length+' instalaciones de Callao y Chancay con su estado publicado e historial.'+(dif?' '+dif+' presentan diferencias entre ambos registros.':'')+'</p>';
    } else if(id==='acp-gatun') {
      body='<p>Calado Neopanamax: <strong>'+esc(number(d?.neopanamaxDraftFeet)||'Sin lectura válida')+(number(d?.neopanamaxDraftFeet)?' pies':'')+'</strong></p><p>Gatún: '+esc(number(d?.lakeLevelFeet)||'Sin lectura válida')+' pies. Fecha del documento: '+esc(date(d?.documentDate))+'</p>';
    } else {
      body='<h3>'+esc(latest?.title||'Sin publicación comprobada')+'</h3><p>Emisión: '+esc(date(latest?.publishedAt||s?.sourceDate))+'</p>';
      if(latest?.validFrom||latest?.validUntil)body+='<p>Vigencia declarada: '+esc(date(latest.validFrom))+' a '+esc(date(latest.validUntil))+'</p>';
      if(latest?.nextReleaseDate)body+='<p>Próximo boletín anunciado: '+esc(date(latest.nextReleaseDate))+'</p>';
      if(id==='acp-advisories')body+='<p>El índice detecta avisos nuevos. Los cupos y condiciones deben leerse en el aviso aplicable al viaje.</p>';
    }
    const error=s?.error?'<p class="live-error">'+esc(s.error)+'</p>':'';
    return '<article class="source-card live-source-card"><div class="source-card-top"><span class="publisher">'+esc(s?.name||id)+'</span>'+pill(h.label,h.tone)+'</div>'+body+error+'<div class="live-audit-times"><span>Último intento: '+esc(time(s?.checkedAt))+'</span><span>Último éxito: '+esc(time(s?.lastSuccessAt))+'</span><span>Cambio detectado: '+esc(time(s?.changedAt))+'</span></div>'+link('Fuente original',latest?.url||d?.documentUrl||s?.url)+'</article>';
  }
  function statusText(value){
    const v=norm(value);
    if(/parcial|partial/.test(v))return {label:'Cierre parcial',tone:'amber'};
    if(/cerrad|closed|cierre/.test(v))return {label:'Cerrado',tone:'red'};
    if(/abiert|open/.test(v))return {label:'Abierto',tone:'green'};
    return {label:value||'Sin estado',tone:'gray'};
  }
  function stateCell(status,level){const x=statusText(status);return pill(x.label,x.tone)+(level?'<span class="live-cell-detail">'+esc(level)+'</span>':'');}
  function portTable(){
    const s=source('dicapi'),all=Array.isArray(s?.data?.ports)?s.data.ports:[];
    const rows=all.filter(p=>state.portFilter==='Todas'||norm(p.captaincy).includes(norm(state.portFilter)));
    return '<section class="live-ports"><div class="section-heading"><div><h2>Estados DICAPI por instalación</h2><p>Estado publicado y último registro histórico. Un estado de una instalación no se extiende a todo el puerto.</p></div>'+stamp('dicapi')+'</div><div class="filter-chips" role="group" aria-label="Filtrar instalaciones">'+['Todas','Callao','Chancay'].map(p=>'<button class="filter-chip '+(state.portFilter===p?'active':'')+'" data-port-filter="'+p+'" aria-pressed="'+(state.portFilter===p)+'">'+p+' ('+(p==='Todas'?all.length:all.filter(r=>norm(r.captaincy).includes(norm(p))).length)+')</button>').join('')+'</div>'+(rows.length?'<div class="table-wrap"><table class="live-port-table"><thead><tr><th>Instalación</th><th>Estado publicado</th><th>Último historial</th><th>Fecha del historial</th><th>Resolución enlazada</th></tr></thead><tbody>'+rows.map(p=>{
      const latest=p.latest;
      return '<tr class="'+(p.needsValidation||p.discrepancy?'live-conflict':'')+'"><td><strong>'+esc(p.name)+'</strong><span class="live-cell-detail">'+esc(p.captaincy)+'</span>'+(p.discrepancy?pill('Diferencia por conciliar','amber'):p.needsValidation?pill('Registro por validar','amber'):'')+'</td><td>'+stateCell(p.status,p.level)+'</td><td>'+(latest?stateCell(latest.status,latest.level):pill(p.historyStatus==='failed'?'Consulta fallida':'Sin historial verificable','amber'))+'</td><td>'+esc(time(latest?.publishedAt))+'</td><td>'+(latest?.resolutionUrl?link(latest.resolutionNumber||'Ver resolución',latest.resolutionUrl):'<span class="muted">Sin enlace verificable</span>')+(p.discrepancy&&p.resolutionUrl?'<span class="live-cell-detail">Ficha actual: '+link(p.resolutionNumber||'Ver resolución',p.resolutionUrl)+'</span>':'')+'<span class="live-cell-detail">Contenido del PDF pendiente de validación</span></td></tr>';
    }).join('')+'</tbody></table></div>':'<div class="empty"><h3>Sin registros DICAPI disponibles</h3><p>No se presume apertura. Consulta el portal oficial.</p>'+link('Consultar DICAPI','https://www.dicapi.mil.pe/estado-de-puertos','button secondary')+'</div>')+'<p class="live-scope">Las discrepancias, fallos y antigüedad quedan visibles. Antes de operar, concilia la instalación, la resolución y su alcance con la terminal o capitanía.</p></section>';
  }
  function view(){
    return '<section class="page-head"><div><div class="eyebrow">FUENTES PÚBLICAS · CONSULTA PROGRAMADA</div><h1>Monitoreo de fuentes</h1><p>Consulta automática cada hora, con fecha de emisión, último intento y último éxito. Los datos llegan cuando el organismo los publica.</p></div><div class="head-actions"><button class="button secondary" data-action="export-live">Descargar última consulta</button></div></section>'+banner(false)+portTable()+'<section class="compare-section"><div class="section-heading"><div><h2>Conectividad y publicaciones</h2><p>Una consulta correcta indica acceso a la fuente. La fecha y vigencia del documento se muestran por separado.</p></div></div><div class="source-grid live-sources">'+EXPECTED.map(sourceCard).join('')+'</div></section><section class="methodology"><h3>Alcance del monitoreo</h3><p>GitHub ejecuta la consulta horaria y esta pantalla lee el resultado cada 5 minutos. Las ejecuciones pueden demorarse. Si una fuente falla, se conserva el último dato exitoso con su fecha y el error visible. El catálogo de rutas conserva su revisión documental del 7 de noviembre de 2026. Itinerarios, espacios, costos y aceptación de conexiones requieren confirmación por embarque.</p></section>';
  }
  function topbar(){const el=document.getElementById('monitor-status');if(!el)return;const old=hours(state.snapshot?.generatedAt)>2;el.textContent=state.busy?'Comprobando fuentes…':state.transport==='error'?'Copia sin comprobar':old?'Datos sin renovar':'Consulta '+time(state.snapshot?.generatedAt);el.className='monitor-top '+(old||state.transport==='error'?'warning':'');}
  window.MARTILINE_MONITOR={refresh,banner,kpis,focus,view,stamp,health,statusText,validSnapshot,getSnapshot:()=>state.snapshot,selectPort(value){state.portFilter=['Todas','Callao','Chancay'].includes(value)?value:'Todas';emit();},topbar};
  window.addEventListener('martiline:live',topbar);
  window.addEventListener('online',refresh);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible' && (!state.receivedAt||hours(state.receivedAt)>5/60))refresh();});
  setInterval(()=>{if(document.visibilityState==='visible')refresh();},5*60*1000);
  setTimeout(refresh,0);
})();
