#!/usr/bin/env node
// Optional READ-ONLY production-path check. No wallet, no keys, no writes.
// Run: LEADERBOARD_LIVE_RPC=https://bsc.rpc.blxrbdn.com node tests/leaderboard-chain.live.test.mjs
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const rpcUrl = process.env.LEADERBOARD_LIVE_RPC;
if (!rpcUrl) {
  console.log('SKIP 设置 LEADERBOARD_LIVE_RPC 后运行生产只读验收');
  process.exit(0);
}
// Browser-path guard: a Node-only JSON-RPC test can pass even when the browser is
// blocked by CORS. OPTIONS and POST must both expose ACAO=* for this public origin.
const corsResponse = await fetch(rpcUrl, {
  method: 'OPTIONS',
  headers: {
    Origin: 'https://agent-id-checkin-dapp.bixiaowu.workers.dev',
    'Access-Control-Request-Method': 'POST',
    'Access-Control-Request-Headers': 'content-type',
  },
});
assert.equal(corsResponse.headers.get('access-control-allow-origin'), '*', '生产 RPC 必须允许浏览器跨域读取');
await corsResponse.arrayBuffer();

const here = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(join(here, '..', 'app.js'), 'utf8');
const nodeRequire = createRequire(import.meta.url);
const ethers = nodeRequire(process.env.ETHERS_MODULE_PATH || '/home/bixiaowuhome/42space-agent/script/42space/node_modules/ethers');
const abiMatch = appSource.match(/const\s+CHECKIN_EVENT_ABI\s*=\s*\[(['"])(.+?)\1\];/);
assert.ok(abiMatch, '未能从生产 app.js 读取 CHECKIN_EVENT_ABI');
const iface = new ethers.Interface([abiMatch[2]]);
const sandbox = { module: { exports: {} }, exports: {}, window: {}, console, setTimeout, clearTimeout };
sandbox.exports = sandbox.module.exports;
vm.runInNewContext(appSource, sandbox, { filename: join(here, '..', 'app.js') });
const app = sandbox.module.exports;
const deploy = app.LEADERBOARD_DEPLOY_BLOCK;
const chunk = app.LEDGER_LOG_CHUNK;
assert.ok(chunk > 0 && chunk <= 1000, `生产分片必须 ≤1000，当前 ${chunk}`);
const appRpcMatch = appSource.match(/const\s+RPC_URL\s*=\s*['"]([^'"]+)['"]/);
assert.ok(appRpcMatch, '未能从生产 app.js 读取 RPC_URL');
assert.equal(appRpcMatch[1], rpcUrl, 'LEADERBOARD_LIVE_RPC 必须与生产 app.js 的 RPC_URL 一致');

const provider = new ethers.JsonRpcProvider(rpcUrl);
const address = '0x8d314451f99bffdac9d2d51c683a5b9aecc1ecb7';
const topic = iface.getEvent('CheckedIn').topicHash;

const latest = await provider.getBlockNumber();
const logs = [];
let chunks = 0;
for (let from = deploy; from <= latest; from += chunk) {
  const to = Math.min(from + chunk - 1, latest);
  const part = await provider.getLogs({ address, topics: [topic], fromBlock: from, toBlock: to });
  assert.ok(Array.isArray(part), 'getLogs 必须返回数组');
  logs.push(...part);
  chunks++;
}
const expected = [
  ['0x147c94fbedb9dd5fceb4e1d45fdf9874a5b890874c11acda17ca27cea13ddd68', 0x8047e20],
  ['0xb1bf169ed06eed6f338e9befc9cb0bb060215f2f1ba2a6fc32bdc7bb259a5d7e', 0x804843d],
  ['0x838f19d5ce815781202b50a9a1f1c7b19821d8125efca83d1fe01380fab62220', 0x8048445],
  ['0x859f5cbaafb4572c48ee464ae11047b5c61d50489c8e9e5a7483eabf01b739b2', 0x804844d],
];
assert.equal(logs.length, 6, `真实 CheckedIn 应为 6 条，实得 ${logs.length}`);
for (const [tx, block] of expected) {
  const hit = logs.find(l => l.transactionHash.toLowerCase() === tx);
  assert.ok(hit, `缺少首签交易 ${tx.slice(0, 12)}…`);
  assert.equal(Number(hit.blockNumber), block);
  const parsed = iface.parseLog(hit);
  assert.equal(parsed.args.day.toString(), '20776');
  assert.equal(parsed.args.streak.toString(), '1');
}
const cur = await provider.getBlock('latest');
app.setBlockResolver(n => provider.getBlock(n));
const rows = await app.aggregateLeaderboardLogs(logs, e => iface.parseLog(e), cur.timestamp, app.ENTRY_WINDOW_START, app.ENTRY_WINDOW_END);
assert.equal(rows.length, 6);
console.log(`PASS 生产只读路径：${chunks} 片、${logs.length} 条真实事件、${rows.length} 行排行榜、四笔首签 tx/day/streak 全匹配`);
