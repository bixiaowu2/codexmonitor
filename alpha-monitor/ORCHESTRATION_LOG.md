# 会话协调记录 / ORCHESTRATION LOG

> 两个 Claude 会话在操作同一台服务器(140.238.38.82)。本文件记录**已做的改动**，
> 避免重复劳动或互相覆盖。**动手前请先读这里。**

## 2026-10-13 00:0x UTC — 会话A(桌面端)完成

### 一、修掉另一会话报的三个问题(其中1个已先行修复)

| 报告的问题 | 实际状态 | 处理 |
|---|---|---|
| `meme_scan.py:205` KeyError(每小时崩) | **已在 16:22 由会话A修复** | 日志证实: 15:03/16:02 崩溃, **18:01 起全部 Deactivated successfully**。无需再动 |
| `radar-maintenance.service` 从未成功 | ✅ 属实 | 已修(见下) |
| `meme-radar.sqlite` 1.1G 且涨 | ✅ 属实 | 已收敛(见下) |

### 二、radar-maintenance.service 修复

**根因**(与会话B判断一致): 告警写进 `DATABASES['alpha']` 的 outbox, 指望 alpha-radar 投递;
但 alpha-radar 已停用、其数据目录已删除 → **四套雷达的运维告警(磁盘满/备份失败/报告异常)全部静默丢失**。
另外 `ReadWritePaths` 含已删除的 `/var/lib/alpha-radar` → systemd 226/NAMESPACE 起不来。

**修复**:
1. drop-in `/etc/systemd/system/radar-maintenance.service.d/fix-paths.conf`(空值重置 ReadWritePaths)
2. `/opt/radar-maintenance/radar_maintenance.py`(备份 `.pre-alpha-removal-20261013`):
   - `DATABASES` 去掉 alpha; `SERVICES` 去掉 alpha-radar
   - **告警改为直发 Telegram**(原 alpha outbox 通道已失效), 状态存 `/var/lib/radar-maintenance/alerts.json`
   - `prune_raw` 不再指向已删除目录
3. 验证: 手动 + systemd 运行 **退出码 0**, `backup_errors: []`

> 同类问题: `radar-holdings.service` 早前也是同因(ReadWritePaths), 已用同样 drop-in 修好。

### 三、meme DB 收敛

- `signals` 保留期 **90天 → 30天**(`storage.py`, 备份 `.pre-30d-20261013`)
  - 原因: 90天会让 DB 涨到 ~2GB; 30天封顶 ~1.4GB
- VACUUM 一次(16.2s)
- **现状**: DB 仍 1.1G = 实际数据(pairs 20万行/7天 + signals 16万行), 非碎片(故 VACUUM 未缩小)
- 如需更小: 可把 pairs 7天→3天 或 signals 30天→14天(会损失研究数据, 未擅自改)

### 四、持仓止盈止损(会话B提的"最值钱一条")— 已落地

**判断**: `positions.py` 与 `demon_track` **互补而非重复**:
- `positions.py` 盯**你实际买入的仓**(按你的成本价, 15%止损/+30%后20%移动/1.5·2·3x分批)
- `demon_track` 盯**候选池**(买前信号)

**新增** `/opt/alpha-radar/position_track.py`(复用 positions.py 的成熟阈值, 但独立运行、文件登记):
```bash
# 登记持仓
sudo /opt/akshare-venv/bin/python3 /opt/alpha-radar/position_track.py --add KOMA 0.0195 [止损%] [移动%]
# 查看
... --list
# 检查(由 position-track.timer 每5分钟自动跑)
... --run
```
- 阈值: 15% 固定止损 / 浮盈+30%后启用20%移动止盈 / 1.5x·2x·3x 分批提示
- 数据: `/var/lib/demon-radar/positions.json`(可直接编辑)
- 定时: `position-track.timer` 每5分钟
- 实测: 登记 KOMA@0.05 → 正确触发止损

**未做**(待用户决定, 需要设计): 把 `/buy` Telegram 命令接过来(现在是文件登记)。

### 五、本轮其他改动

- **cashtag 修复**: 统一大写; 不再清洗符号造假的(原 `A-1`→`$A1`); 允许数字开头(`1INCH`)
- **cashtag 上限**: 单帖最多 3 个(实测 >3 报 `220095`)
- **去掉帖子结尾 `$`**: 原 `14.5M$` 与开头 cashtag 的 `$` 构成 `$...$`, 被当数学公式导致不链接
- **meme 推送加合约地址**(用户要求): 信号帖/观察池/日更都带
- **meme_track 日更 KeyError 修复**: 用了不存在的 `_tier`
- **meme_publish.py**(新): meme 观察池自动发广场 + X 草稿
- **meme-rush 接入**: meme_scan 用币安官方 meme-rush(集中度+聪明钱), 候选 8→59
- **meme S 级加风险过滤**: 狙击手≥50% 或 dev≥20% 降级

## 给会话B的提示

- **别动** `meme_scan.py` / `radar_maintenance.py` / `storage.py` / `square_publish.py` / `position_track.py` —— 会话A刚改完并验证
- 三个服务的 `deliver_one()` 已被代码层禁用(只采集不推送), 推送统一走 scan/track 系列 —— **这是有意设计, 别"修复"它**
- 磁盘 69%, 33G 可用

## 未解决/建议下一步

1. **四套系统共同缺回测** —— 阈值全是手工拍定; `forward.py` 在 meme 侧已 no-op(无消费者)。
   建议: 用已有历史数据(或继续用币安 openInterestHist/fundingRate)做阈值回测。
2. **X 全自动**: X 免额度是一次性的(不按月刷新), 但发帖仅 $0.015/条(纯文字)。
   用户加支付卡可拿 $20 免费额度(≈1330条) → 加完即可切全自动。
3. **回测/阈值文档**: README 里关于 forward 账本的说法在 meme 侧已不成立(已 no-op), 需更正。
