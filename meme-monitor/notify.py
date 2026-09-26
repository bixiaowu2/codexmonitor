from __future__ import annotations
import base64,hashlib,hmac,json,time,urllib.parse
from net import post_json

def ding_url(url,secret):
 if not secret:return url
 ts=str(int(time.time()*1000));sig=base64.b64encode(hmac.new(secret.encode(),(ts+'\n'+secret).encode(),hashlib.sha256).digest()).decode();p=urllib.parse.urlsplit(url);q=urllib.parse.parse_qs(p.query);q.update({'timestamp':[ts],'sign':[sig]});return urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,urllib.parse.urlencode(q,doseq=True),''))

def send_channel(cfg,text,channel):
 if channel=='telegram' and cfg.telegram_enabled:
  r=post_json('https://api.telegram.org/bot'+cfg.telegram_token+'/sendMessage',{'chat_id':cfg.telegram_chat,'text':text},cfg.timeout)
  if not isinstance(r,dict) or r.get('ok') is not True:raise RuntimeError('telegram_rejected')
 elif channel=='dingtalk' and cfg.dingtalk_enabled:
  body={'msgtype':'text','text':{'content':cfg.dingtalk_keyword+' · Meme雷达\n'+text},'at':{'isAtAll':False}}
  r=post_json(ding_url(cfg.dingtalk_webhook,cfg.dingtalk_secret),body,cfg.timeout)
  if not isinstance(r,dict) or r.get('errcode')!=0:raise RuntimeError('dingtalk_rejected')

 else:raise RuntimeError("channel_disabled")
