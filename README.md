# codexmonitor

监控程序集合。每个程序放在独立目录中，分别管理依赖、配置和部署。

| 程序 | 说明 | 状态 |
| --- | --- | --- |
| [x-monitor](x-monitor/) | X 账号监控，推送至 Telegram 和钉钉 | 已加入 |
| `alpha-monitor/` | 币安 Alpha 监控 | 待加入 |

## 使用 X 监控

```bash
git clone https://github.com/bixiaowu2/codexmonitor.git
cd codexmonitor/x-monitor
```

然后按 [X 监控说明](x-monitor/README.md) 或 [Oracle ARM 部署说明](x-monitor/ORACLE_ARM.md) 安装。

后续将 Alpha 程序放入仓库根目录下的 `alpha-monitor/`，与 `x-monitor/` 并列。两个程序分别保存自己的 `.env`、依赖和运行状态。

## 私密配置

不要提交实际 `.env`、机器人 Token、webhook、SSH 私钥、浏览器会话或运行状态。根目录的 `.gitignore` 对各程序子目录同样生效；配置模板使用 `.env.example`。
