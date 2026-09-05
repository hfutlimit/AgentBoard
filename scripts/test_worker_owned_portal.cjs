// DOM integration tests for the embedded page, using the frontend's installed jsdom.
// No browser automation, provider execution, production network or credentials.
const {JSDOM} = require('../src/frontend/node_modules/jsdom');
const {readFileSync} = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = readFileSync(path.join(__dirname,'../src/nodes/AgentBoard.Node/WorkerOwned/ConfigurationPortal.html'),'utf8');
let saved,revision='v1';
const initial={enabled:true,reconcileSeconds:5,projects:[{projectId:16,localPath:'E:\\sample'}],agents:[
 {id:'a',provider:'codex',enabled:true,workKinds:['dev'],projectIds:[16],prePrompt:'common-before',postPrompt:'common-after',prompts:{dev:{pre:'dev-before',post:'dev-after'}},runtime:{command:'codex',arguments:['exec','--json'],model:'terra',timeoutMinutes:30}},
 {id:'b',provider:'codex',enabled:true,workKinds:['qa'],projectIds:[16],prePrompt:'b-before',postPrompt:'b-after',prompts:{},runtime:{command:'codex',arguments:['exec'],model:'terra',timeoutMinutes:30}}
]};
const dom = new JSDOM(html,{url:'http://127.0.0.1:18240/',runScripts:'dangerously',beforeParse(w){
 w.confirm=()=>true;
 w.fetch=async(url,request)=>{
  assert.equal(request.headers['X-AgentBoard-Worker-Key'],undefined);
  assert.equal(request.headers['X-AgentBoard-Local-Portal'],'1');
  let result;
  if(url.endsWith('/configuration')){
   if(request.method==='PUT'){const body=JSON.parse(request.body);assert.equal(body.revision,revision);saved=body.configuration;revision='v2'}
   result={configuration:structuredClone(saved||initial),revision};
  }else if(url.endsWith('/status'))result={configurationOnly:true,serverUrl:'http://prod.test',apiCredentialConfigured:true,brokerConfigured:true,brokerHost:'mq.test',workerId:'local',configPath:'local.json'};
  else if(url.endsWith('/projects'))result={items:[{id:16,name:'Real project shape'}],total:1};
  else throw Error('Unexpected request '+url);
  return{ok:true,text:async()=>JSON.stringify(result)};
 }; }});
const w=dom.window,d=w.document;
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const change=(selector,value)=>{const el=d.querySelector(selector);el.value=value;el.dispatchEvent(new w.Event('input',{bubbles:true}));el.dispatchEvent(new w.Event('change',{bubbles:true}))};
(async()=>{
 await flush();
 assert.equal(w.location.hash,'');
 assert.equal(d.querySelector('#login'),null);
 assert.equal(d.querySelector('#main').classList.contains('hidden'),false);
 assert.equal(d.querySelectorAll('[data-kind]').length,7);
 assert.match(d.querySelector('#connection').textContent,/prod.test/);
 assert.match(d.querySelector('[data-map-id]').textContent,/Real project shape/);
 change('#pre','通用 pre 编辑');change('#scope','dev');
 assert.equal(d.querySelector('#pre').value,'dev-before');
 change('#post','开发 post 编辑');
 d.querySelector('[data-agent="1"]').click();
 assert.equal(d.querySelector('#pre').value,'b-before');
 d.querySelector('[data-agent="0"]').click();
 assert.equal(d.querySelector('#pre').value,'通用 pre 编辑');
 change('#scope','dev');assert.equal(d.querySelector('#post').value,'开发 post 编辑');
 d.querySelector('#save').click();await flush();
 assert.equal(saved.agents[0].prePrompt,'通用 pre 编辑');
 assert.equal(saved.agents[0].prompts.dev.post,'开发 post 编辑');
 assert.equal(saved.agents[1].prePrompt,'b-before');
 assert.match(d.querySelector('#message').textContent,/重启/);
 d.querySelector('#reload').click();await flush();
 assert.equal(d.querySelector('#pre').value,'通用 pre 编辑');
 change('#scope','dev');assert.equal(d.querySelector('#post').value,'开发 post 编辑');
 d.querySelector('#addAgent').click();assert.equal(d.querySelectorAll('[data-agent]').length,3);
 change('#provider','workbuddy');assert.equal(d.querySelector('#command').value,'workbuddy');
 assert.equal(d.querySelector('#arguments').value,'--print\n--output-format\njson');
 d.querySelector('#removeAgent').click();assert.equal(d.querySelectorAll('[data-agent]').length,2);
 dom.window.close();console.log('PASS: seven kinds, production project shape, prompt scopes, independent profiles, save/reload, provider switch, add/remove.');
})().catch(e=>{dom.window.close();console.error(e);process.exitCode=1});
