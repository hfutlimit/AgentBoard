// DOM integration tests for the embedded page, using the frontend's installed jsdom.
// No browser automation, provider execution, production network or credentials.
const {JSDOM} = require('../src/frontend/node_modules/jsdom');
const {readFileSync} = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = readFileSync(path.join(__dirname,'../src/nodes/AgentBoard.Node/WorkerOwned/ConfigurationPortal.html'),'utf8');
let saved,revision='v1',addFailure=false,addRequests=0,runtimeState='stopped',startRequests=0,stopRequests=0;
const initial={enabled:true,reconcileSeconds:5,projects:[{projectId:16,localPath:'E:\\sample'}],agents:[
 {id:'a',provider:'codex',enabled:true,workKinds:['dev'],projectIds:[16],prePrompt:'common-before',postPrompt:'common-after',prompts:{dev:{pre:'dev-before',post:'dev-after'}},runtime:{command:'codex',arguments:['exec','--json'],model:'terra',timeoutMinutes:30}},
 {id:'b',provider:'codex',enabled:true,workKinds:['qa'],projectIds:[16],prePrompt:'b-before',postPrompt:'b-after',prompts:{},runtime:{command:'codex',arguments:['exec'],model:'terra',timeoutMinutes:30}}
]};
const dom = new JSDOM(html,{url:'http://127.0.0.1:18240/',runScripts:'dangerously',beforeParse(w){
 w.confirm=()=>true;
 w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','')};
 w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open')};
 w.fetch=async(url,request)=>{
  assert.equal(request.headers['X-AgentBoard-Worker-Key'],undefined);
  assert.equal(request.headers['X-AgentBoard-Local-Portal'],'1');
  let result;
   if(url.includes('/agents/a/work-records?'))result={items:[{recordId:'0123456789abcdef0123456789abcdef',workId:17,agentId:'a',workKind:'dev',businessItem:'task #17',state:'succeeded',deliveryState:'confirmed',startedAt:'2026-09-07T00:00:00Z',endedAt:'2026-09-07T00:01:00Z',summary:'Structured dev result'}],nextCursor:null};
   else if(url.includes('/agents/b/work-records?'))result={items:[{recordId:'fedcba9876543210fedcba9876543210',workKind:'qa',businessItem:'task #18',state:'failed',deliveryState:'not_applicable',startedAt:'2026-09-07T00:02:00Z',endedAt:'2026-09-07T00:03:00Z',summary:'Safe failure code'}],nextCursor:null};
   else if(url.endsWith('/work-records/0123456789abcdef0123456789abcdef?agentId=a'))result={summary:{recordId:'0123456789abcdef0123456789abcdef',workId:17,agentId:'a',workKind:'dev',businessItem:'task #17',state:'succeeded',deliveryState:'confirmed',startedAt:'2026-09-07T00:00:00Z',endedAt:'2026-09-07T00:01:00Z'},provider:'codex',model:'gpt-5.6-sol',resultDetail:'Result: abcdef',events:[{occurredAt:'2026-09-07T00:00:00Z',state:'running'}]};
   else if(url.endsWith('/work-records/fedcba9876543210fedcba9876543210?agentId=b'))result={summary:{recordId:'fedcba9876543210fedcba9876543210',workId:18,agentId:'b',workKind:'qa',businessItem:'task #18',state:'failed',deliveryState:'not_applicable',startedAt:'2026-09-07T00:02:00Z',endedAt:'2026-09-07T00:03:00Z'},provider:'codex',model:'gpt-5.6-sol',failureCode:'ProviderFailed',events:[{occurredAt:'2026-09-07T00:02:00Z',state:'failed'}]};
   else if(url.endsWith('/configuration')){
   if(request.method==='PUT'){const body=JSON.parse(request.body);assert.equal(body.revision,revision);saved=body.configuration;revision='v2'}
   result={configuration:structuredClone(saved||initial),revision};
  }else if(url.endsWith('/agents')){
   addRequests++;if(addFailure)return{ok:false,text:async()=>JSON.stringify({detail:'Simulated save conflict'})};
   const body=JSON.parse(request.body);assert.equal(body.revision,revision);
   saved=structuredClone(saved||initial);saved.agents.push({id:body.id,provider:body.provider,enabled:false,workKinds:[],prompts:{},prePrompt:'',postPrompt:'',runtime:{command:body.provider,model:body.model,arguments:['exec','--json'],timeoutMinutes:30}});
   revision='created-'+addRequests;result={configuration:structuredClone(saved),revision};
  }else if(url.endsWith('/runtime/start')){assert.equal(request.method,'POST');startRequests++;runtimeState='starting';result={state:runtimeState}};
  if(url.endsWith('/runtime/stop')){assert.equal(request.method,'POST');stopRequests++;runtimeState='stopping';result={state:runtimeState}};
  if(url.endsWith('/runtime'))result={state:runtimeState};
  else if(url.endsWith('/status'))result={configurationOnly:true,serverUrl:'http://prod.test',apiCredentialConfigured:true,brokerConfigured:true,brokerHost:'mq.test',workerId:'local',configPath:'local.json'};
  else if(url.endsWith('/projects'))result={items:[{id:16,name:'Real project shape'},{id:17,name:'Second project'}],total:2};
  else if(!result)throw Error('Unexpected request '+url);
  return{ok:true,text:async()=>JSON.stringify(result)};
 }; }});
const w=dom.window,d=w.document;
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const change=(selector,value)=>{const el=d.querySelector(selector);el.value=value;el.dispatchEvent(new w.Event('input',{bubbles:true}));el.dispatchEvent(new w.Event('change',{bubbles:true}))};
(async()=>{
 let releaseProjects;
 const projectsPending=new Promise(resolve=>{releaseProjects=resolve});
 const blockedDom=new JSDOM(html,{url:'http://127.0.0.1:18242/',runScripts:'dangerously',beforeParse(w){
  w.confirm=()=>true;
  w.fetch=async(url,request)=>{
   assert.equal(request.headers['X-AgentBoard-Local-Portal'],'1');
   if(url.endsWith('/configuration'))return{ok:true,text:async()=>JSON.stringify({configuration:structuredClone(initial),revision:'blocked'})};
   if(url.endsWith('/status'))return{ok:true,text:async()=>JSON.stringify({configurationOnly:true,serverUrl:'http://prod.test',apiCredentialConfigured:true,brokerConfigured:true,brokerHost:'mq.test',workerId:'local',configPath:'local.json'})};
   if(url.endsWith('/projects'))return projectsPending;
   if(url.endsWith('/runtime'))return{ok:true,text:async()=>JSON.stringify({state:'stopped'})};
   throw Error('Unexpected pending-project request '+url);
  };
 }});
 try{
  await flush();await flush();
  assert.equal(blockedDom.window.document.querySelectorAll('[data-agent]').length,2,
   'local agents should render while production projects are pending');
 }finally{
  releaseProjects({ok:true,text:async()=>JSON.stringify({items:[]})});
  await flush();await flush();
  blockedDom.window.close();
 }
 await flush();await flush();await flush();await flush();
 assert.equal(w.location.hash,'');
 assert.equal(d.querySelector('#login'),null);
 const favicon=d.querySelector('link[rel="icon"]');
 assert.equal(favicon.type,'image/svg+xml');
 assert.match(decodeURIComponent(favicon.getAttribute('href')),/>AB<\/text>/);
 assert.match(favicon.getAttribute('href'),/^data:image\/svg\+xml,/);
 assert.equal(d.querySelector('#main').classList.contains('hidden'),false);
 d.querySelector('[data-editor-tab="kinds"]').click();
 assert.equal(d.querySelectorAll('[data-kind]').length,7);
 assert.match(d.querySelector('#connection').textContent,/prod.test/);
 assert.match(d.querySelector('#mappings').textContent,/Real project shape/);
 assert.equal(d.querySelectorAll('[data-project]').length,0);
 d.querySelector('#projectsTab').click();
 assert.equal(w.location.hash,'#projects');
 assert.equal(d.querySelector('#mappingPanel').classList.contains('hidden'),false);
 assert.equal(d.querySelector('#agentSidebar').classList.contains('hidden'),true);
 d.querySelector('#addProject').click();
 assert.equal(d.querySelector('#projectDialog').open,true);
 change('#mappingProject','17');
 change('#mappingPath','E:\\second-project');
 d.querySelector('#projectForm').dispatchEvent(new w.Event('submit',{cancelable:true}));
 assert.equal(d.querySelectorAll('.mapping').length,2);
 d.querySelector('#agentsTab').click();
 assert.equal(d.querySelector('#mappingPanel').classList.contains('hidden'),true);
 assert.equal(d.querySelectorAll('[data-project]').length,0);
 d.querySelector('[data-editor-tab="records"]').click();await flush();await flush();
 assert.match(d.querySelector('#editor').textContent,/task #17/);
 assert.match(d.querySelector('#editor').textContent,/1 分钟 0 秒/);
 d.querySelector('[data-record-id]').click();await flush();await flush();
 assert.match(d.querySelector('#editor').textContent,/gpt-5.6-sol/);
 assert.match(d.querySelector('#editor').textContent,/工作 ID：17/);
 d.querySelector('[data-history-back]').click();await flush();
 d.querySelector('[data-editor-tab="prompts"]').click();
 change('#pre','通用 pre 编辑');change('#scope','dev');
 assert.equal(d.querySelector('#pre').value,'dev-before');
 change('#post','开发 post 编辑');
 d.querySelector('[data-agent="1"]').click();
 d.querySelector('[data-editor-tab="records"]').click();await flush();await flush();
 assert.match(d.querySelector('#editor').textContent,/task #18/);
 assert.doesNotMatch(d.querySelector('#editor').textContent,/task #17/);
 d.querySelector('[data-record-id]').click();await flush();await flush();
 assert.match(d.querySelector('#editor').textContent,/ProviderFailed/);
 d.querySelector('[data-history-back]').click();await flush();
 d.querySelector('[data-editor-tab="prompts"]').click();
 assert.equal(d.querySelector('#pre').value,'b-before');
 d.querySelector('[data-agent="0"]').click();
 assert.equal(d.querySelector('#pre').value,'通用 pre 编辑');
 change('#scope','dev');assert.equal(d.querySelector('#post').value,'开发 post 编辑');
 d.querySelector('#save').click();await flush();
 assert.equal(saved.agents[0].prePrompt,'通用 pre 编辑');
 assert.equal(saved.agents[0].prompts.dev.post,'开发 post 编辑');
 assert.equal(saved.agents[1].prePrompt,'b-before');
 assert.equal(saved.projects[1].localPath,'E:\\second-project');
 assert.match(d.querySelector('#message').textContent,/启动/);
 d.querySelector('#reload').click();await flush();
 assert.equal(d.querySelector('#pre').value,'通用 pre 编辑');
 change('#scope','dev');assert.equal(d.querySelector('#post').value,'开发 post 编辑');
 d.querySelector('[data-editor-tab="basic"]').click();
 const options=selector=>[...d.querySelector(selector).options].filter(o=>!o.disabled).map(o=>o.value);
 assert.deepEqual(options('#model'),['gpt-5.6-terra','gpt-5.6-sol','gpt-5.6-luna']);
 d.querySelector('#addAgent').click();assert.equal(d.querySelectorAll('[data-agent]').length,2);
 assert.equal(d.querySelector('#addAgentDialog').open,true);
 change('#newProvider','workbuddy');assert.deepEqual(options('#newModel'),['hy4-preview','glm-5.3-flash']);
 change('#newProvider','minimax');assert.deepEqual(options('#newModel'),['m3']);
 d.querySelector('#cancelAddAgent').click();assert.equal(d.querySelector('#addAgentDialog').open,false);
 assert.equal(addRequests,0);
 d.querySelector('[data-editor-tab="prompts"]').click();change('#post','Unsubmitted existing edits');
 d.querySelector('#addAgent').click();change('#newAgentId','a');
 const submit=()=>d.querySelector('#addAgentForm').dispatchEvent(new w.Event('submit',{cancelable:true}));
 submit();await flush();assert.match(d.querySelector('#addAgentError').textContent,/已存在/);assert.equal(addRequests,0);
 change('#newAgentId','new-codex');change('#newModel','gpt-5.6-sol');addFailure=true;
 submit();await flush();assert.equal(d.querySelectorAll('[data-agent]').length,2);
 assert.equal(d.querySelector('#addAgentDialog').open,true);assert.match(d.querySelector('#addAgentError').textContent,/Simulated save conflict/);
 addFailure=false;submit();submit();await flush();
 assert.equal(addRequests,2);assert.equal(d.querySelectorAll('[data-agent]').length,3);
 assert.equal(d.querySelector('#addAgentDialog').open,false);
 d.querySelector('[data-editor-tab="basic"]').click();
 assert.equal(d.querySelector('#model').value,'gpt-5.6-sol');
 assert.equal(d.querySelector('#agentEnabled').checked,false);
 assert.equal(saved.agents[2].enabled,false);
 assert.equal(saved.agents[0].prompts.dev.post,'开发 post 编辑');
 d.querySelector('[data-agent="0"]').click();d.querySelector('[data-editor-tab="prompts"]').click();change('#scope','dev');assert.equal(d.querySelector('#post').value,'Unsubmitted existing edits');
 d.querySelector('[data-agent="2"]').click();d.querySelector('[data-editor-tab="basic"]').click();
 change('#provider','workbuddy');assert.equal(d.querySelector('#command').value,'codebuddy');
 assert.deepEqual(options('#model'),['hy4-preview','glm-5.3-flash']);
 assert.equal(d.querySelector('#arguments').value,'-p\n-y\n--output-format\ntext');
 change('#model','glm-5.3-flash');assert.equal(d.querySelector('#model').value,'glm-5.3-flash');
 change('#provider','minimax');assert.deepEqual(options('#model'),['m3']);
 d.querySelector('#removeAgent').click();assert.equal(d.querySelectorAll('[data-agent]').length,2);
 assert.equal(d.querySelector('#startWorker').disabled,false);
 d.querySelector('#startWorker').click();d.querySelector('#startWorker').click();await flush();await flush();
 assert.equal(startRequests,1);assert.equal(d.querySelector('#startWorker').disabled,true);
 assert.match(d.querySelector('#runtimeStatus').textContent,/正在启动/);
 assert.equal(d.querySelector('#stopWorker').disabled,false);
 d.querySelector('#stopWorker').click();await flush();await flush();
 assert.equal(stopRequests,1);assert.match(d.querySelector('#runtimeStatus').textContent,/正在停止/);
 assert.equal(d.querySelector('#startWorker').disabled,true);
 const direct=new JSDOM(html,{url:'http://127.0.0.1:18240/#agents/b/work-records/fedcba9876543210fedcba9876543210',runScripts:'dangerously',beforeParse(w){
  w.confirm=()=>true;w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','')};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open')};
  w.fetch=async(url,request)=>{assert.equal(request.headers['X-AgentBoard-Local-Portal'],'1');let result;
   if(url.endsWith('/configuration'))result={configuration:structuredClone(initial),revision:'direct'};
   else if(url.endsWith('/status'))result={configurationOnly:true,serverUrl:'http://prod.test',apiCredentialConfigured:true,brokerConfigured:true,brokerHost:'mq.test',workerId:'local',configPath:'local.json'};
   else if(url.endsWith('/projects'))result={items:[]};
   else if(url.endsWith('/runtime'))result={state:'stopped'};
   else if(url.endsWith('/work-records/fedcba9876543210fedcba9876543210?agentId=b'))result={summary:{recordId:'fedcba9876543210fedcba9876543210',workId:18,agentId:'b',workKind:'qa',businessItem:'task #18',state:'failed',deliveryState:'not_applicable',startedAt:'2026-09-07T00:02:00Z',endedAt:'2026-09-07T00:03:00Z'},provider:'codex',model:'gpt-5.6-sol',failureCode:'ProviderFailed',events:[]};
   else throw Error('Unexpected direct-route request '+url);return{ok:true,text:async()=>JSON.stringify(result)};
  };
 }});
 await flush();await flush();await flush();
 assert.equal(direct.window.document.querySelector('.agent.selected strong').textContent.trim(),'b');
 assert.match(direct.window.document.querySelector('#editor').textContent,/ProviderFailed/);
 direct.window.close();
 dom.window.close();console.log('PASS: seven kinds, production project shape, prompt scopes, independent profiles, save/reload, provider switch, add/remove.');
})().catch(e=>{dom.window.close();console.error(e);process.exitCode=1});
