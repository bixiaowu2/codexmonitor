#!/usr/bin/env node
// Optional READ-ONLY production-path check for BOTH public RPCs. No wallet, no keys, no writes.
// Run: node tests/leaderboard-chain.live.test.mjs
// Each RPC is probed independently; at least one must return the six real events.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

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
const sources = app.LEADERBOARD_RPCS;
assert.deepEqual(sources.map(s => [s.url, s.chunk]), [
  ['https://bsc-rpc.publicnode.com', 45000],
  ['https://bsc.rpc.blxrbdn.com', 1000],
], '双路 RPC/分片配置必须符合裁定');
assert.equal(app.RPC_URL, sources[0].url);
const deploy = app.LEADERBOARD_DEPLOY_BLOCK;
const address = '0x8d314451f99bffdac9d2d51c683a5b9aecc1ecb7';
const topic = iface.getEvent('CheckedIn').topicHash;
const expected = [
  ['0x147c94fbedb9dd5fceb4e1d45fdf9874a5b890874c11acda17ca27cea13ddd68', 0x8047e20],
  ['0xb1bf169ed06eed6f338e9befc9cb0bb060215f2f1ba2a6fc32bdc7bb259a5d7e', 0x804843d],
  ['0x838f19d5ce815781202b50a9a1f1c7b19821d8125efca83d1fe01380fab62220', 0x8048445],
  ['0x859f5cbaafb4572c48ee464ae11047b5c61d50489c8e9e5a7483eabf01b739b2', 0x804844d],
];

function verifyEvents(logs, label) {
  assert.equal(logs.length, 6, `${label} 真实 CheckedIn 应为 6 条，实得 ${logs.length}`);
  for (const [tx, block] of expected) {
    const hit = logs.find(l => l.transactionHash.toLowerCase() === tx);
    assert.ok(hit, `${label} 缺少首签交易 ${tx.slice(0, 12)}…`);
    assert.equal(Number(hit.blockNumber), block);
    const parsed = iface.parseLog(hit);
    assert.equal(parsed.args.day.toString(), '20776');
    assert.equal(parsed.args.streak.toString(), '1');
  }
}

const results = [];
for (const source of sources) {
  try {
    // CORS guard: browser path needs ACAO=* on both OPTIONS and POST.
    const cors = await fetch(source.url, {
      method: 'OPTIONS',
      headers: {
        Origin: 'https://agent-id-checkin-dapp.bixiaowu.workers.dev',
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'content-type',
      },
    });
    assert.equal(cors.headers.get('access-control-allow-origin'), '*', `${source.name} 必须允许浏览器跨域`);
    await cors.arrayBuffer();

    const provider = new ethers.JsonRpcProvider(source.url);
    const latest = await provider.getBlockNumber();
    const logs = [];
    let chunks = 0;
    for (let from = deploy; from <= latest; from += source.chunk) {
      const to = Math.min(from + source.chunk - 1, latest);
      const part = await provider.getLogs({ address, topics: [topic], fromBlock: from, toBlock: to });
      assert.ok(Array.isArray(part), 'getLogs 必须返回数组');
      logs.push(...part);
      chunks++;
    }
    verifyEvents(logs, source.name);
    const cur = await provider.getBlock('latest');
    app.setBlockResolver(n => provider.getBlock(n));
    const rows = await app.aggregateLeaderboardLogs(logs, e => iface.parseLog(e), cur.timestamp, app.ENTRY_WINDOW_START, app.ENTRY_WINDOW_END);
    assert.equal(rows.length, 6, `${source.name} 排行榜应为 6 行`);
    results.push(`${source.name}: ${chunks} 片/${logs.length} 事件/${rows.length} 行 PASS`);
  } catch (e) {
    results.push(`${source.name}: FAIL (${e.message.split('\n')[0]})`);
  }
}
console.log('双路生产只读验收：' + results.join(' | '));
assert.ok(results.some(r => r.endsWith('PASS')), '两路公共 RPC 全部失败，生产路径不可用');
