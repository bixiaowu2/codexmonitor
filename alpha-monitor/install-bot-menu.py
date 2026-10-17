import sys,json,urllib.request
sys.path.insert(0,'/opt/alpha-radar')
from cloud import Config,NoRedirect
c=Config.load()
commands=[{'command':'help','description':'查看信号规则与持仓登记用法'},{'command':'status','description':'查看扫描和纸面观察状态'},{'command':'positions','description':'查看已登记的实际现货持仓'},{'command':'buy','description':'登记已买入的地址与均价，不下单'},{'command':'close','description':'结束指定持仓提醒，不执行卖出'}]
req=urllib.request.Request('https://api.telegram.org/bot'+c.token+'/setMyCommands',data=json.dumps({'commands':commands,'scope':{'type':'chat','chat_id':c.chat}}).encode(),headers={'Content-Type':'application/json'})
try:
 with urllib.request.build_opener(NoRedirect()).open(req,timeout=20) as r:p=json.loads(r.read(65536))
 print('Telegram command menu configured' if p.get('ok') else 'Telegram command menu rejected')
except Exception:print('Telegram command menu request failed; secrets hidden');raise SystemExit(1)
