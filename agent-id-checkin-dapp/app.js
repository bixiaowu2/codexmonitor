// 生产前端：浏览器注入钱包；合约已部署于 BNB Smart Chain 主网。
const CONTRACT_ADDRESS = '0x8d314451f99bffdac9d2d51c683a5b9aecc1ecb7';
const CONTRACT_ABI = [
  'function checkIn(uint256 tokenId)',
  'function hasCheckedInToday(uint256 tokenId) view returns (bool)',
  'function streakOf(uint256 tokenId) view returns (uint256)',
  'function lastCheckInDay(uint256 tokenId) view returns (uint256)',
  'function agentId() view returns (address)'
];
const CHECKIN_EVENT_ABI = ['event CheckedIn(uint256 indexed tokenId, uint256 indexed day, uint256 streak)'];
const ENTRY_WINDOW_START = Date.UTC(2026, 10, 18, 8);  // 2026-11-18T08:00Z，投稿期起
const ENTRY_WINDOW_END = Date.UTC(2026, 11, 9, 8);    // 2026-12-09T08:00Z，投稿期止
const RPC_URL = 'https://bsc.rpc.blxrbdn.com';  // 只读公共 RPC；钱包签名仍走 injected provider
const DATA_URL = './data/latest.json';
const AGENT_ID_CONTRACT = '0x60b9262456de011f7d15a54cdd8c48baaa8b022f';

const state = { provider:null, signer:null, address:'', tokenId:'' };
const $=id=>document.getElementById(id);

function keccak256Utf8(str){
  if (window.ethers && ethers.keccak256 && ethers.toUtf8Bytes) return ethers.keccak256(ethers.toUtf8Bytes(str));
  throw new Error('ethers 未加载，无法计算 tokenId');
}

async function connect(){
  try{
    if(!window.ethereum) throw new Error('未检测到浏览器注入钱包（请用钱包内置浏览器打开）');
    await ethereum.request({method:'eth_requestAccounts'});
    const provider = new ethers.BrowserProvider(window.ethereum);
    state.provider=provider; state.signer=await provider.getSigner(); state.address=await state.signer.getAddress();
    const chain=await provider.getNetwork();
    if(BigInt(chain.chainId)!==56n) throw new Error('请切换到 BNB Chain 主网（chainId 56）');
    const code=await provider.getCode(AGENT_ID_CONTRACT);
    if(code==='0x') throw new Error('当前网络查不到 Agent ID 合约');
    const c=new ethers.Contract(AGENT_ID_CONTRACT,['function balanceOf(address) view returns (uint256)'],provider);
    const bal=await c.balanceOf(state.address);
    if(BigInt(bal)<=0n) throw new Error('该地址未持有 SPACE ID Agent ID');
    state.tokenId=keccak256Utf8('bixiaowu'); // TODO: 实际用户名由钱包/输入确定；当前为四台自家昵称占位
    $('status').textContent=`已连接 ${state.address.slice(0,6)}…${state.address.slice(-4)} · Agent ID tokenId=${state.tokenId.slice(0,10)}…（测试版昵称占位）`;
    $('checkin').disabled=!CONTRACT_ADDRESS;
    if(!CONTRACT_ADDRESS) $('status').textContent+=' · 合约未部署，签到按钮暂不可用';
    await loadLeaderboard();
  }catch(e){ $('status').textContent='连接失败：'+e.message; }
}

async function checkin(){
  try{
    if(!CONTRACT_ADDRESS) throw new Error('合约地址未配置');
    const c=new ethers.Contract(CONTRACT_ADDRESS,CONTRACT_ABI,state.signer);
    const has=await c.hasCheckedInToday(state.tokenId);
    if(!has){ const tx=await c.checkIn(state.tokenId); $('status').textContent='交易已提交：'+tx.hash; await tx.wait(); }
    const streak=await c.streakOf(state.tokenId);
    $('status').textContent=`签到完成 · 连续 ${streak} 天`;
    await Promise.all([loadData(), loadLeaderboard()]);
  }catch(e){ $('status').textContent='签到失败：'+e.message; }
}

function render(items, cols){
  if(!Array.isArray(items)||!items.length) return '<p class="muted">暂无数据</p>';
  return '<table><thead><tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr></thead><tbody>'+
    items.map(r=>'<tr>'+cols.map(c=>`<td>${r[c[0]]??'—'}</td>`).join('')+'</tr>').join('')+'</tbody></table>';
}


function esc(s){
  return String(s ?? '').replace(/[&<>'"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function shortAddr(a){ return a && a.length>10 ? a.slice(0,6)+'…'+a.slice(-4) : (a||'—'); }

async function leaderboardProvider(){
  return new ethers.JsonRpcProvider(RPC_URL);
}

// Public BSC RPCs reject `fromBlock:0 -> latest` event queries (archive-token
// limits / -32005). Use the deployment block as a fixed lower bound and walk
// bounded ranges; each window stays small enough for key-free public nodes.
const LEADERBOARD_DEPLOY_BLOCK = 0x8047035;
const LEDGER_LOG_CHUNK = 1000;

async function fetchLeaderboardLogs(provider, address, topic){
  const latest=await provider.getBlockNumber();
  const logs=[];
  for(let from=LEADERBOARD_DEPLOY_BLOCK; from<=latest; from+=LEDGER_LOG_CHUNK){
    const to=Math.min(from+LEDGER_LOG_CHUNK-1, latest);
    const part=await provider.getLogs({address, topics:[topic], fromBlock:from, toBlock:to});
    logs.push(...part);
  }
  return logs;
}

function ledgerLeaderboard(){
  // Deterministic offline fixture for release checks: never used when contract/RPC is configured.
  const raw=localStorage.getItem('agentid_leaderboard_fixture');
  if(!raw) return null;
  const rows=JSON.parse(raw);
  if(!Array.isArray(rows)) throw new Error('排行榜测试数据必须是数组');
  const safe=rows.map(r=>({token:String(r.token||'').slice(0,80), streak:Math.max(0,Math.floor(Number(r.streak)||0)), last:String(r.last||'').slice(0,10)})).filter(r=>r.token && r.last);
  safe.sort((a,b)=>b.streak-a.streak || b.last.localeCompare(a.last));
  return {rows:safe, events:safe.length, sources:safe.length};
}

function renderLeaderboard(rows, events, sources){
  const board=$('board'), counters=$('counters');
  counters.innerHTML='<span>参与 tokenId：'+sources+'</span><span>签到事件：'+events+'</span><span>当前连续 ≥2 天：'+rows.filter(r=>r.streak>=2).length+'</span>';
  board.innerHTML=rows.length ? '<table><thead><tr><th>tokenId</th><th>当前连续天数</th><th>最近签到</th></tr></thead><tbody>'+
    rows.slice(0,20).map(r=>'<tr><td>'+esc(shortAddr(r.token))+'</td><td>'+esc(r.streak)+'</td><td>'+esc(r.last)+'</td></tr>').join('')+'</tbody></table>'
    : '<p class="muted">投稿期内暂无签到事件</p>';
}

// Pure aggregation of CheckedIn logs -> leaderboard rows. Exposed on window
// only so isolated tests can inject fixed RPC-shaped logs; production path passes
// real logs from provider.getLogs and is otherwise unchanged.
async function aggregateLeaderboardLogs(logs, parseLog, curTimestamp, entryStartMs, entryEndMs){
  const byToken=new Map();
  for(const log of logs){
    const e=parseLog(log);
    const token=e.args.tokenId.toString();
    const prev=byToken.get(token);
    if(!prev || log.blockNumber>prev.block) byToken.set(token,{block:log.blockNumber, streak:Number(e.args.streak)});
  }
  const rows=[];
  for(const [token,last] of byToken){
    const b=await blockByNumberForLeaderboard(last.block);
    const day=Math.floor((b.timestamp-entryStartMs/1000)/86400)+1;
    const today=Math.floor((curTimestamp-entryStartMs/1000)/86400)+1;
    const inWindow=b.timestamp*1000>=entryStartMs && b.timestamp*1000<=entryEndMs;
    if(!inWindow) continue;
    const active=day===today || day===today-1;
    const streak=active ? last.streak : 0;
    rows.push({token,streak,last:new Date(b.timestamp*1000).toISOString().slice(0,10)});
  }
  rows.sort((a,b)=>b.streak-a.streak || b.last.localeCompare(a.last));
  return rows;
}

// Injectable block resolver; production overrides this with provider.getBlock.
let blockByNumberForLeaderboard = async () => { throw new Error('block resolver not configured'); };

async function loadLeaderboard(){
  const box=$('leaderboard');
  box.classList.remove('hidden');
  const fixture=ledgerLeaderboard();
  if(fixture){ renderLeaderboard(fixture.rows, fixture.events, fixture.sources); return; }
  if(!CONTRACT_ADDRESS){ $('board').innerHTML='<p class="muted">合约未配置，排行榜暂不可用</p>'; $('counters').textContent=''; return; }
  try{
    const provider=await leaderboardProvider();
    if(!provider.getLogs) throw new Error('当前 provider 不支持事件查询');
    const c=new ethers.Contract(CONTRACT_ADDRESS,CHECKIN_EVENT_ABI,provider);
    const topic=c.filters.CheckedIn().topics[0];
    const logs=await fetchLeaderboardLogs(provider, CONTRACT_ADDRESS, topic);
    blockByNumberForLeaderboard = (n) => provider.getBlock(n);
    const cur=await provider.getBlock('latest');
    const rows=await aggregateLeaderboardLogs(logs, e=>c.interface.parseLog(e), cur.timestamp, ENTRY_WINDOW_START, ENTRY_WINDOW_END);
    renderLeaderboard(rows, logs.length, rows.length);
  }catch(e){ $('board').innerHTML='<p class="muted">排行榜读取失败：'+esc(e.message)+'</p>'; $('counters').textContent=''; }
}

async function loadData(){
  const d=await (await fetch(DATA_URL,{cache:'no-store'})).json();
  $('data-date').textContent=d.date||''; $('generated-at').textContent='生成时刻：'+(d.generated_at||'');
  const s=d.sections||{};
  $('sections').innerHTML =
    '<h3>股票精选（'+((s.stocks&&s.stocks.count)||0)+'）</h3>'+render(s.stocks&&s.stocks.items,[['symbol','代码'],['name','名称'],['tier','档位'],['conc','置信度']])+
    '<h3>Meme 高分候选（'+((s.meme&&s.meme.count)||0)+'）</h3>'+render(s.meme&&s.meme.items,[['symbol','符号'],['score','评分'],['tier','档位'],['chain','链']])+
    '<h3>X 监控当日帖（'+((s.x&&s.x.count)||0)+'）</h3>'+render(s.x&&s.x.items,[['created_at','时间'],['views','曝光'],['text','文本']]);
  $('panel').classList.remove('hidden');
}

// Test-only export: absent in browsers (no module.exports), used by the
// isolated Node harness to inject fixed RPC-shaped logs.
if(typeof module!=='undefined' && module.exports){
  module.exports = { aggregateLeaderboardLogs, setBlockResolver: fn => { blockByNumberForLeaderboard = fn; }, ENTRY_WINDOW_START, ENTRY_WINDOW_END, LEADERBOARD_DEPLOY_BLOCK, LEDGER_LOG_CHUNK, fetchLeaderboardLogs };
}

if(typeof document!=='undefined' && $('connect') && $('checkin')){
  $('connect').addEventListener('click',connect);
  $('checkin').addEventListener('click',checkin);
  loadData().catch(e=>{$('sections').innerHTML='<p>数据加载失败：'+e.message+'</p>';$('panel').classList.remove('hidden');});
  loadLeaderboard();
}
