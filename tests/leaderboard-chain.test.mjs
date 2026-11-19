#!/usr/bin/env node
// Isolated read-only leaderboard path tests. No network, no wallet, no keys.
// Run: node tests/leaderboard-chain.test.mjs
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const appPath = join(here, '..', 'app.js');
const appSource = readFileSync(appPath, 'utf8');

// Prefer a normal install; the absolute fallback is this machine's local ethers,
// kept out of app.js and never used by the browser. No keys or network involved.
const nodeRequire = createRequire(import.meta.url);
function loadEthers() {
  const candidates = [process.env.ETHERS_MODULE_PATH, 'ethers', '/home/bixiaowuhome/42space-agent/script/42space/node_modules/ethers'].filter(Boolean);
  let lastError;
  for (const id of candidates) {
    try { return nodeRequire(id); } catch (error) { lastError = error; }
  }
  throw new Error(`ethers 未安装（可设 ETHERS_MODULE_PATH）：${lastError?.message || lastError}`);
}
const ethers = loadEthers();

function abiFromProductionApp() {
  const match = appSource.match(/const\s+CHECKIN_EVENT_ABI\s*=\s*\[(['"])(.+?)\1\];/);
  if (!match) throw new Error('未能从 app.js 读取 CHECKIN_EVENT_ABI');
  return [match[2]];
}

const prodIface = new ethers.Interface(abiFromProductionApp());
const oldIface = new ethers.Interface(['event CheckedIn(uint256 indexed tokenId, uint256 day, uint256 streak)']);

function encodeRealCheckIn(tokenId, day, streak) {
  const iface = new ethers.Interface(['event CheckedIn(uint256 indexed tokenId, uint256 indexed day, uint256 streak)']);
  return iface.encodeEventLog(iface.getEvent('CheckedIn'), [tokenId, day, streak]);
}

function rpcLogFrom(encoded, blockNumber) {
  return { blockNumber, topics: encoded.topics, data: encoded.data };
}

// Load app.js in a CommonJS-like sandbox: no document, so page init is skipped;
// module.exports receives the pure helper. window.ethers is unused by these tests.
function loadApp() {
  const sandbox = { module: { exports: {} }, exports: {}, window: {}, console, setTimeout, clearTimeout };
  sandbox.exports = sandbox.module.exports;
  vm.runInNewContext(appSource, sandbox, { filename: appPath });
  return sandbox.module.exports;
}

const { aggregateLeaderboardLogs, setBlockResolver, ENTRY_WINDOW_START, ENTRY_WINDOW_END } = loadApp();

// Fixed fixtures: BSC-like blocks within the 2026-11-18 08:00Z entry window.
const DAY = 86400;
const BASE = ENTRY_WINDOW_START / 1000 + 3600; // 2026-11-18 09:00:00Z
const NOW = BASE + 2 * DAY;                    // 2026-11-20 09:00:00Z (inside window)
const blocks = {
  100: { timestamp: BASE },
  101: { timestamp: BASE + DAY },
  102: { timestamp: BASE + 2 * DAY },
  999: { timestamp: BASE - 10 * DAY }, // outside window (negative control)
};

// VM-realm objects fail deepStrictEqual prototypes; compare plain data.
const plain = value => JSON.parse(JSON.stringify(value));

function parseLog(log) {
  return { args: { tokenId: BigInt(log.tokenId), streak: BigInt(log.streak) } };
}

async function withResolver(resolver, fn) {
  setBlockResolver(resolver);
  try { return await fn(); } finally { setBlockResolver(() => { throw new Error('block resolver not configured'); }); }
}

let passed = 0;
async function test(name, fn) { await fn(); passed++; console.log('PASS  ' + name); }

await test('正例：生产 ABI 解码真实事件日志 → tokenId/day/streak 正确', async () => {
  const encoded = encodeRealCheckIn(1n, 20776n, 1n);
  assert.equal(encoded.topics.length, 3);
  assert.equal((encoded.data.length - 2) / 2, 32);
  const decoded = prodIface.parseLog(rpcLogFrom(encoded, 100));
  assert.equal(decoded.args.tokenId.toString(), '1');
  assert.equal(decoded.args.day.toString(), '20776');
  assert.equal(decoded.args.streak.toString(), '1');
});

await test('反例：旧 ABI 解码真实事件日志必须失败（证明旧测试是假通过）', async () => {
  const encoded = encodeRealCheckIn(1n, 20776n, 1n);
  assert.throws(() => oldIface.parseLog(rpcLogFrom(encoded, 100)), /data out-of-bounds/);
});

await test('正例：有 CheckedIn 事件 → 聚合出 tokenId/连续天数/日期', async () => {
  const logs = [
    { blockNumber: 100, tokenId: '8361', streak: '1' },
    { blockNumber: 101, tokenId: '8361', streak: '2' }, // later log wins
    { blockNumber: 102, tokenId: '9999', streak: '1' },
  ];
  const rows = await withResolver(n => blocks[n], () =>
    aggregateLeaderboardLogs(logs, parseLog, NOW, ENTRY_WINDOW_START, ENTRY_WINDOW_END));
  assert.deepEqual(plain(rows), [
    { token: '8361', streak: 2, last: new Date((BASE + DAY) * 1000).toISOString().slice(0, 10) },
    { token: '9999', streak: 1, last: new Date((BASE + 2 * DAY) * 1000).toISOString().slice(0, 10) },
  ]);
});

await test('反例：无事件 → 空排行榜', async () => {
  const rows = await withResolver(n => blocks[n], () =>
    aggregateLeaderboardLogs([], parseLog, NOW, ENTRY_WINDOW_START, ENTRY_WINDOW_END));
  assert.deepEqual(plain(rows), []);
});

await test('反例：查询失败（块读取抛错）→ 错误传播，不静默出假数据', async () => {
  await assert.rejects(
    withResolver(() => { throw new Error('RPC timeout'); }, () =>
      aggregateLeaderboardLogs(
        [{ blockNumber: 100, tokenId: '8361', streak: '1' }],
        parseLog, NOW, ENTRY_WINDOW_START, ENTRY_WINDOW_END)),
    /RPC timeout/);
});

await test('反例：窗口外事件被过滤', async () => {
  const rows = await withResolver(n => blocks[n], () =>
    aggregateLeaderboardLogs(
      [{ blockNumber: 999, tokenId: '0xaaa', streak: '1' }],
      parseLog, NOW, ENTRY_WINDOW_START, ENTRY_WINDOW_END));
  assert.deepEqual(plain(rows), []);
});

console.log(`\n${passed}/${passed} PASS — leaderboard chain read path (offline fixtures)`);
