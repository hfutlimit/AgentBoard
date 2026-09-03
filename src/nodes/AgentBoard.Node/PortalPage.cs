namespace AgentBoard.Node;

public static class PortalPage
{
    // The page is intentionally dependency-free so it is available on an isolated worker PC.
    public const string Html = """
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentBoard Worker</title>
<style>body{font:14px system-ui;margin:2rem;max-width:1200px;color:#172033}button,input{padding:.45rem;margin:.2rem}table{border-collapse:collapse;width:100%;margin-top:1rem}td,th{border-bottom:1px solid #d9dfeb;padding:.55rem;text-align:left}pre{white-space:pre-wrap;background:#101827;color:#e6edf6;padding:1rem;max-height:26rem;overflow:auto}.hidden{display:none}.bad{color:#a11}</style></head>
<body><h1>AgentBoard Node</h1><p>Enter the local portal key. It is kept only in this browser session.</p><input id="key" type="password" placeholder="Portal API key"><button onclick="connect()">Connect</button><span id="error" class="bad"></span>
<section id="main" class="hidden"><h2>Worker</h2><pre id="status"></pre><button onclick="post('/api/control/pause')">Pause consumption</button><button onclick="post('/api/control/resume')">Resume consumption</button>
<h2>Execution history</h2><table><thead><tr><th>ID</th><th>Workload</th><th>Round</th><th>Source</th><th>Status</th><th>Started</th><th></th></tr></thead><tbody id="rows"></tbody></table>
<h2>Execution detail</h2><pre id="detail">Select an execution.</pre></section>
<script>let key=sessionStorage.k||'';key&&(document.querySelector('#key').value=key);const h=()=>({'X-AgentBoard-Worker-Key':key});async function get(u){let r=await fetch(u,{headers:h()});if(!r.ok)throw Error(await r.text());return r.json()}async function post(u){let r=await fetch(u,{method:'POST',headers:h()});if(!r.ok)alert(await r.text());await refresh()}async function connect(){key=document.querySelector('#key').value;sessionStorage.k=key;try{await refresh();document.querySelector('#main').classList.remove('hidden');document.querySelector('#error').textContent=''}catch(e){document.querySelector('#error').textContent='Authentication failed: '+e.message}}async function refresh(){document.querySelector('#status').textContent=JSON.stringify(await get('/api/worker'),null,2);let a=await get('/api/executions');document.querySelector('#rows').innerHTML=a.map(x=>`<tr><td>${x.id}</td><td>${x.workloadType}:${x.workloadId}</td><td>${x.round}</td><td>${x.source}</td><td>${x.status}</td><td>${x.startedAt}</td><td><button onclick="show(${x.id})">Detail</button>${x.status==='failed'?`<button onclick="post('/api/executions/${x.id}/retry')">Retry</button>`:''}</td></tr>`).join('')}async function show(id){document.querySelector('#detail').textContent=JSON.stringify(await get('/api/executions/'+id),null,2)}setInterval(()=>key&&refresh().catch(()=>{}),10000)</script></body></html>
""";
}
