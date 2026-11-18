// 测试版前端：浏览器注入钱包；合约接口留桩，地址/ABI 由 lead 部署后填入。
const CONTRACT_ADDRESS = ''; // TODO: 部署后填
const CONTRACT_ABI = [];      // TODO: 部署后填 checkIn/hasCheckedInToday/streakOf/lastCheckInDay
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
    await loadData();
  }catch(e){ $('status').textContent='签到失败：'+e.message; }
}

function render(items, cols){
  if(!Array.isArray(items)||!items.length) return '<p class="muted">暂无数据</p>';
  return '<table><thead><tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr></thead><tbody>'+
    items.map(r=>'<tr>'+cols.map(c=>`<td>${r[c[0]]??'—'}</td>`).join('')+'</tr>').join('')+'</tbody></table>';
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

$('connect').addEventListener('click',connect);
$('checkin').addEventListener('click',checkin);
loadData().catch(e=>{$('sections').innerHTML='<p>数据加载失败：'+e.message+'</p>';$('panel').classList.remove('hidden');});
