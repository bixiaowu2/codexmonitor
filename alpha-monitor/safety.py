"""Current third-party risk snapshot; missing evidence is never a clean bill of health."""
import json,time
from pathlib import Path
from radar import num,PublicAPI

URL='https://api.gopluslabs.io/api/v1/token_security/56'
FLAGS={'is_honeypot':'供应商标记疑似蜜罐','cannot_sell_all':'供应商标记无法全部卖出','transfer_pausable':'可暂停转账','is_blacklisted':'存在黑名单能力','owner_change_balance':'所有者可修改余额','hidden_owner':'隐藏所有者','slippage_modifiable':'可修改交易税','is_mintable':'存在增发能力'}

def summarize(raw):
    flags=[label for key,label in FLAGS.items() if str(raw.get(key))=='1']
    for key in ('buy_tax','sell_tax'):
        tax=num(raw.get(key))
        if tax is not None and tax>.1:flags.append(key+'超过10%')
    holders=raw.get('holders') or []
    labels=[{'address':h.get('address'),'label':str(h.get('tag'))[:120],'share':num(h.get('percent')),'attribution':'third_party_label_not_control_proof'} for h in holders if isinstance(h,dict) and h.get('tag')][:5]
    values=[num(h.get('percent')) for h in holders[:10] if isinstance(h,dict)]
    values=[x for x in values if x is not None and 0<=x<=1]
    return {'provider':'GoPlus','flags':flags,'provider_honeypot_flag':raw.get('is_honeypot'),
            'raw_top10_share':sum(values) if len(values)==10 else None,
            'entity_labels':labels,'holders_returned':len(holders),'holder_count':num(raw.get('holder_count')),
            'cleaned_concentration':'unknown','related_wallets':'unknown','unlock_schedule':'unknown',
            'status':'risk_detected' if flags else ('no_listed_risk_detected' if raw.get('is_honeypot')=='0' else 'unknown'),
            'note':'当前第三方快照，非历史前因；前十项未剔除池/桥/交易所，不能直接解释为控盘；代理合约/权限可变化，不保证可卖出。'}

def inspect(address,folder,now=None):
    now=time.time() if now is None else now
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);path=folder/(address+'.json')
    try:
        cached=json.loads(path.read_text())
        ttl=600 if cached.get('status')=='unknown' else 3600
        if 0<=now-cached['observed']<ttl:return cached
    except (OSError,ValueError,KeyError):pass
    try:
        # A single attempt with short bounds; unavailable risk sources must not stall exits.
        import subprocess
        r=subprocess.run(['curl','--fail','--silent','--show-error','--connect-timeout','4','--max-time','8',URL+'?contract_addresses='+address],capture_output=True,timeout=10)
        if r.returncode:raise ValueError('risk_source_unavailable')
        response=json.loads(r.stdout);raw=response.get('result',{}).get(address)
        if response.get('code')!=1 or not isinstance(raw,dict) or not raw:raise ValueError('risk_source_missing')
        result=summarize(raw)
    except Exception:
        result={'status':'unknown','provider':'GoPlus','flags':[],'cleaned_concentration':'unknown','related_wallets':'unknown','unlock_schedule':'unknown','raw_top10_share':None}
    result.update(observed=now,source=URL+'?contract_addresses='+address)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(result));tmp.replace(path)
    return result

def unlock_override(address,folder,now=None):
    """Optional sourced manual schedule; malformed or old disclosures remain unknown."""
    now=time.time() if now is None else now
    try:
        v=json.loads((Path(folder)/'risk-overrides.json').read_text()).get(address,{})
        reviewed=num(v.get('reviewed_at'));event=num(v.get('unlock_at'));fraction=num(v.get('fraction_of_circulating'))
        source=v.get('source','')
        if reviewed is None or not 0<=now-reviewed<=7*86400 or event is None or fraction is None or not 0<=fraction<=100 or not isinstance(source,str) or not source.startswith('https://'):return {'status':'unknown'}
        return {'status':'manual_sourced_schedule','source':source,'unlock_at':event,'fraction_of_circulating':fraction,'large_unlock_soon':0<=event-now<=7*86400 and fraction>=.05,'reviewed_at':reviewed}
    except (OSError,ValueError,AttributeError,TypeError):return {'status':'unknown'}


def entity_evidence(address,folder,now=None):
    """Store sourced claims without promoting an investor/label into a market controller."""
    now=time.time() if now is None else now
    try:
        claims=json.loads((Path(folder)/'risk-overrides.json').read_text()).get(address,{}).get('entities',[])
        result=[]
        for c in claims[:10]:
            reviewed=num(c.get('reviewed_at'));source=c.get('source','');name=c.get('name','');role=c.get('role','')
            if reviewed is None or not 0<=now-reviewed<=30*86400 or not isinstance(source,str) or not source.startswith('https://'):continue
            if role not in ('investor','market_maker','treasury','custodian','exchange','wallet_label','alleged_controller'):continue
            if not isinstance(name,str) or not name.strip():continue
            result.append({'name':name[:80],'role':role,'source':source[:500],'reviewed_at':reviewed,
                           'wallets':[str(a)[:100] for a in c.get('wallets',[])[:10]],
                           'confidence':'sourced_claim_not_independently_verified','score_contribution':0})
        return result
    except (OSError,ValueError,AttributeError,TypeError):return []
