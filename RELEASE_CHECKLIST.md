# 发布前检查清单（本地，未获总指挥正式裁定前不发布）

- [ ] 总指挥正式裁定已到（回应用户 16:15Z e9a1 授权信与 16:17Z 3bd2 回执）
- [ ] gas 钱包、上限、用户实际签名确认点已书面确认；home 不代持、不导出、不转发密钥/助记词/配对码
- [ ] 合约地址、ABI、BscScan 验证链接与 owner 权限披露（本项目按原信为无 owner）已回填
- [ ] 公开仓库名、README、许可证与数据脱敏口径已确认
- [ ] 发布渠道、文案、标签与问卷链接已确认；不冒充 Binance、不承诺收益、不刷量
- [ ] `python3 scripts/scan_secrets.py --root . --strict` 通过（当前失败：`tools/sync_radar_export.py:27` 硬编码私钥路径，必须改参数/环境变量）
- [ ] `node --check app.js` 通过
- [ ] 数据面板：`date`/`generated_at`/`sections.stocks|meme|x` 结构校验通过；静态数据不含敏感字段

当前状态（2026-11-18 16:18Z）：本机已写骨架与扫描器；未 git init、未推送、未部署、未发布、未花 gas。
