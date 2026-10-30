'use strict';
const $ = selector => document.querySelector(selector);
const labels = {language:'Sprache',fallback_language:'Ersatzsprache',dry_run:'Vorschaumodus',replace_existing:'Fremde NFOs ersetzen (mit Sicherung)',poll_seconds:'Prüfintervall (Sekunden)',stable_seconds:'Dateiruhezeit (Sekunden)',retry_seconds:'Wiederholung (Sekunden)',cache_seconds:'API-Cache (Sekunden)',plex_url:'Plex-Serveradresse',tmdb_token:'TMDB Read Access Token',plex_token:'Plex-Token',webhook_token:'WebUI-/Webhook-Token',name:'Name',kind:'Typ',path:'Container-Medienpfad',sonarr_path:'Sonarr-Pfadpräfix',plex_path:'Plex-Pfadpräfix',plex_section:'Plex-Bibliotheks-ID',season_numbering:'TMDB-Staffel-/Folgenreihenfolge erlauben'};
const stateLabels = {pending:'Wartend',running:'In Bearbeitung',done:'Abgeschlossen',failed:'Fehlgeschlagen',superseded:'Durch Änderung ersetzt'};
labels.polling_enabled = 'Automatische Ordnerprüfung';
Object.assign(labels,{ui_auth_enabled:'WebUI-Anmeldung erforderlich (empfohlen)',sonarr_url:'Sonarr-Serveradresse',sonarr_api_key:'Sonarr API-Key',tvdb_api_key:'TheTVDB API-Key',tvdb_pin:'TheTVDB Subscriber-PIN (falls erforderlich)',metadata_source:'Metadatenquelle'});
let offset = 0, total = 0, configLoaded = false, activeTab = 'inventory';
let logsPaused=false, logsBusy=false, logEntries=[];
function renderLogs(){const output=$('#log-output');const follow=output.scrollHeight-output.scrollTop-output.clientHeight<50;const entries=logEntries.filter(entry=>$('#log-level').value==='all'||['WARNING','ERROR','CRITICAL'].includes(entry.level));output.textContent=entries.map(entry=>entry.text).join('\n');if(follow)output.scrollTop=output.scrollHeight;}
async function logs(){if(logsBusy)return;logsBusy=true;try{const data=await api('/api/logs');logEntries=data.entries;renderLogs();$('#log-info').textContent=`${data.entries.length} von maximal ${data.capacity} Meldungen · Stand: ${new Date().toLocaleTimeString('de-DE')}${logsPaused?' · Pausiert':''}`;}finally{logsBusy=false;}}
function element(tag, text, cls) { const node = document.createElement(tag); if(text !== undefined) node.textContent = text; if(cls) node.className = cls; return node; }
function notice(text) { $('#notice').textContent = text; }
async function api(path, body) {
  const options = {credentials:'same-origin',headers:{}};
  if(body !== undefined) {options.method='POST';options.headers={'Content-Type':'application/json','X-Metadachs-UI':'1'};options.body=JSON.stringify(body);}
  const response=await fetch(path,options);const data=await response.json();
  if(!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function guarded(fn) { return async (...args) => {try {await fn(...args);} catch(error) {notice(error.message);} }; }
function updateNfoButton(payload,text='NFO aktualisieren (fehlende IDs)') {const button=element('button',text);button.type='button';button.addEventListener('click',guarded(async()=>{if(!confirm('Vorhandene NFOs aktualisieren? Nur fehlende IDs werden ergänzt; Texte bleiben erhalten. Änderungen werden gesichert. Der Vorschaumodus wird beachtet. Bereits wartende/laufende Aufträge werden übersprungen.'))return;button.disabled=true;try{const result=await api('/api/nfo/update',payload);notice(`${result.queued} Aktualisierungen eingereiht, ${result.skipped} übersprungen.${result.dry_run?' VORSCHAU: keine Schreibzugriffe.':''} Fortschritt unter Aufträge; danach Bestand neu prüfen.`);}finally{button.disabled=false;}}));return button;}
$('#update-library').addEventListener('click',()=>{const library=$('#library').value;if(library)updateNfoButton({library}).click();});
function nfoDetails(nfo) {
  const box=element('div');
  const status={missing:'NFO fehlt',valid:'NFO lesbar',error:'NFO fehlerhaft'};
  box.append(element('strong',status[nfo.status],nfo.status==='valid'?'':'bad'),element('div',nfo.path,'path'));
  if(nfo.error) box.append(element('p',nfo.error,'bad'));
  const badges=element('div');for(const group of nfo.groups) badges.append(element('span',group,'badge'));box.append(badges);
  if(nfo.status==='valid') {
    const detail=element('details');detail.append(element('summary','Felder und Inhalte anzeigen'));
    const table=element('table');for(const [key,values] of Object.entries(nfo.fields)) {const row=element('tr');row.append(element('th',key),element('td',values.join('\n')));table.append(row);}detail.append(table);box.append(detail);
  }
  return box;
}
async function inventory() {
  const query=new URLSearchParams({q:$('#search').value,library:$('#library').value,offset:String(offset),limit:'25'});
  const data=await api('/api/inventory?'+query);total=data.total;
  const chosen=$('#library').value;$('#library').replaceChildren(element('option','Alle Mediatheken'));$('#library').firstChild.value='';
  for(const name of data.libraries){const option=element('option',name);option.value=name;$('#library').append(option);}$('#library').value=chosen;
  $('#update-library').disabled=!chosen;
  $('#inventory-info').textContent=data.scanning?'Bestand wird geprüft …':data.updated?'Stand: '+new Date(data.updated*1000).toLocaleString('de-DE'):'Noch kein Bestandsabgleich';
  $('#inventory-errors').replaceChildren(...data.errors.map(error=>element('p',error,'bad')));
  $('#items').replaceChildren();
  for(const item of data.items){
    const card=element('article',undefined,'card');card.append(element('h3',item.title),element('p',`${item.kind==='tv'?'Serie':'Film'} · ${item.library} · ${item.files.length} Videodatei(en)`,'muted'),element('div',item.path,'path'),nfoDetails(item.nfo));
    card.append(updateNfoButton({path:item.path,library:item.library},item.kind==='tv'?'NFOs der Serie aktualisieren (fehlende IDs)':'NFO aktualisieren (fehlende IDs)'));
    const details=element('details');details.append(element('summary',item.kind==='tv'?'Episoden und ihre NFOs':'Videodateien'));
    for(const file of item.files){const entry=element('details');entry.append(element('summary',file.name));if(file.nfo) entry.append(nfoDetails(file.nfo));else entry.append(element('div',file.path,'path'));entry.append(updateNfoButton({path:file.path,library:item.library}));details.append(entry);}card.append(details);$('#items').append(card);
  }
  if(!data.items.length) $('#items').append(element('p','Keine passenden Medien gefunden. Mounts und Einstellungen prüfen oder Bestandsabgleich abwarten.'));
  $('#page').textContent=`${total?offset+1:0}–${Math.min(offset+25,total)} von ${total}`;$('#previous').disabled=offset===0;$('#next').disabled=offset+25>=total;
}
async function jobs(){const data=await api('/api/jobs');const table=element('table');const header=element('tr');for(const value of ['Mediathek / Datei','Status','Phase','Fehler']) header.append(element('th',value));table.append(header);for(const job of data){const row=element('tr');const name=element('td',job.library);name.append(element('div',job.path,'path'));row.append(name,element('td',stateLabels[job.status]||job.status),element('td',(job.operation==='nfo-update'?'NFO-IDs aktualisieren · ':'')+job.phase),element('td',job.error||'–'));table.append(row);}$('#job-list').replaceChildren(table);}
function inputField(key,value,secret=false){const label=element('label',labels[key]||key);let input;if(key==='kind'||key==='metadata_source'){input=element('select');const choices=key==='kind'?[['tv','Serien'],['movie','Filme']]:[['tmdb','TMDB'],['tvdb','TheTVDB + Sonarr-Dateizuordnung']];for(const [v,text] of choices){const option=element('option',text);option.value=v;input.append(option);}input.value=value||'tmdb';}else{input=element('input');input.type=secret?'password':typeof value==='boolean'?'checkbox':typeof value==='number'?'number':'text';if(input.type==='checkbox') input.checked=value;else input.value=secret?'':String(value??'');if(input.type==='number') input.min='1';if(secret) input.autocomplete='new-password';}input.dataset.key=key;label.append(input);return label;}
function libraryEditor(lib={name:'',kind:'tv',path:'',sonarr_path:'',plex_path:'',plex_section:'',season_numbering:false,metadata_source:'tmdb'}){const box=element('fieldset');box.className='library-editor';box.append(element('legend',lib.name||'Neue Mediathek'));const grid=element('div',undefined,'form-grid');for(const key of ['name','kind','metadata_source','path','sonarr_path','plex_path','plex_section','season_numbering']) grid.append(inputField(key,lib[key]??(key==='season_numbering'?false:'')));box.append(grid);const remove=element('button','Mediathek entfernen');remove.type='button';remove.addEventListener('click',()=>{if(confirm('Mediathek aus der Konfiguration entfernen? Medien werden nicht gelöscht.'))box.remove();});box.append(remove);$('#library-editors').append(box);}
async function settings(){const data=await api('/api/config');$('#general').replaceChildren(...Object.entries(data.settings).map(([key,value])=>inputField(key,value)));$('#secrets').replaceChildren();for(const [key,configured] of Object.entries(data.secrets)){const field=inputField(key,'',true);field.append(element('span',configured?'Bereits hinterlegt':'Noch nicht hinterlegt','muted'));$('#secrets').append(field);}$('#overrides').textContent=data.environment_overrides.length?'Vorrangige Umgebungsvariablen: '+data.environment_overrides.map(key=>'METADACHS_'+key.toUpperCase()).join(', '):'Keine vorrangigen Umgebungsvariablen.';$('#library-editors').replaceChildren();data.libraries.forEach(libraryEditor);if(data.restart_required)notice('Einstellungen gespeichert. Container-Neustart erforderlich; angezeigt wird die gespeicherte Konfiguration.');configLoaded=true;}
function values(parent){const result={};for(const input of parent.querySelectorAll('[data-key]'))result[input.dataset.key]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;return result;}
for(const button of document.querySelectorAll('[data-tab]')) button.addEventListener('click',guarded(async()=>{activeTab=button.dataset.tab;for(const section of document.querySelectorAll('main > section'))section.hidden=section.id!==activeTab;for(const tab of document.querySelectorAll('[data-tab]'))tab.classList.toggle('active',tab===button);if(activeTab==='settings'&&!configLoaded)await settings();if(activeTab==='jobs')await jobs();if(activeTab==='logs'&&!logsPaused)await logs();}));
$('#reload-logs').addEventListener('click',guarded(logs));
$('#pause-logs').addEventListener('click',guarded(async()=>{logsPaused=!logsPaused;$('#pause-logs').textContent=logsPaused?'Fortsetzen':'Pausieren';if(logsPaused)$('#log-info').textContent+=' · Pausiert';else await logs();}));
$('#log-level').addEventListener('change',renderLogs);
setInterval(guarded(async()=>{if(activeTab==='logs'&&!logsPaused&&!document.hidden)await logs();}),3000);
$('#refresh').addEventListener('click',guarded(async()=>{await api('/api/inventory/refresh',{});notice('Lesender Bestandsabgleich angefordert. Es werden dadurch keine NFOs geschrieben.');await inventory();}));
let searchTimer;$('#search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(guarded(async()=>{offset=0;await inventory();}),300);});
$('#library').addEventListener('change',guarded(async()=>{offset=0;await inventory();}));
$('#previous').addEventListener('click',guarded(async()=>{offset=Math.max(0,offset-25);await inventory();}));$('#next').addEventListener('click',guarded(async()=>{offset+=25;await inventory();}));
$('#reload-jobs').addEventListener('click',guarded(jobs));$('#retry').addEventListener('click',guarded(async()=>{if(!confirm('Fehlgeschlagene Aufträge erneut verarbeiten? Im Schreibmodus können NFOs aktualisiert werden.'))return;await api('/api/jobs/retry',{});await jobs();}));
$('#add-library').addEventListener('click',()=>libraryEditor());
$('#config-form').addEventListener('submit',guarded(async event=>{event.preventDefault();const settings=values($('#general'));if(!settings.ui_auth_enabled&&!confirm('ACHTUNG: Ohne WebUI-Anmeldung kann jeder mit Netzwerkzugriff Einstellungen und Zugangsdaten ändern. Nur im vertrauenswürdigen LAN und ohne Portfreigabe verwenden. Fortfahren?'))return;if(!confirm('Einstellungen speichern? Aktivierung erfolgt erst nach Container-Neustart.'))return;await api('/api/config',{settings,secrets:values($('#secrets')),libraries:[...document.querySelectorAll('.library-editor')].map(values)});notice('Gespeichert und gesichert. Bitte Container neu starten. Umgebungsvariablen haben weiterhin Vorrang.');}));
async function status(){const data=await api('/api/status');$('#mode').textContent=data.dry_run?'VORSCHAU – keine Schreibzugriffe':'SCHREIBMODUS';if(!data.ui_auth_enabled)$('#mode').textContent+=' · ACHTUNG: Ohne Anmeldung';if(data.restart_required)$('#mode').textContent+=' · Neustart nötig';}
guarded(async()=>{await status();await inventory();$('[data-tab="inventory"]').classList.add('active');})();
setInterval(guarded(async()=>{await status();if(activeTab==='inventory')await inventory();if(activeTab==='jobs')await jobs();}),15000);