from __future__ import annotations
import math,time

def age_minutes(p,now):
    created=p.get('created_at')
    if not created:return None
    # DEX aggregators expose pairCreatedAt in milliseconds; accept seconds too.
    created=float(created)/1000 if float(created)>10_000_000_000 else float(created)
    return max(0,(now-created)/60)

def score(p,wallet_hits=0,kol_hits=0):
    liq=p.get('liquidity_usd') or 0;v=p.get('volume_1h') or 0; buys=p.get('buys_1h') or 0;sells=p.get('sells_1h') or 0
    if liq<5000 or v<1000:return {'score':0,'stage':'blocked','reasons':['流动性或1小时成交额过低']}
    safety=p.get('safety_status','disabled')
    if safety=='blocked':return {'score':0,'stage':'blocked','reasons':['可卖性/合约安全检查阻断'],'risk':p.get('safety_blocks',[]),'safety_status':safety,'safety_blocks':p.get('safety_blocks',[]),'safety_warnings':p.get('safety_warnings',[])}
    age=age_minutes(p,time.time()); age_bonus=18 if age is not None and age<=180 else 8 if age is not None and age<=1440 else 0
    buy_ratio=buys/max(buys+sells,1) if p.get('buys_1h') is not None and p.get('sells_1h') is not None else .5; flow=18*min(max((buy_ratio-.5)*2,0),1); vol=18*min(v/max(liq,1),4)/4; liq_score=15*min(liq/100000,1); momentum=15*min(max((p.get('change_1h') or 0)/30,0),1)
    wallet=12*min(wallet_hits/3,1); kol=10*min(kol_hits/2,1); scorev=round(min(100,age_bonus+flow+vol+liq_score+momentum+wallet+kol),1)
    reasons=[]
    if age_bonus:reasons.append('早期池')
    if buy_ratio>.6:reasons.append(f'1h买入交易笔数占比{buy_ratio:.0%}')
    if (p.get('change_1h') or 0)>0:reasons.append('1h动能')
    if wallet_hits:reasons.append(f'聪明钱包命中{wallet_hits}')
    if kol_hits:reasons.append(f'KOL提及{kol_hits}')
    risk=['LP锁仓/关联钱包/解锁尚未验证']
    if safety=='disabled':risk.append('合约安全尚未验证')
    if p.get('buys_1h') is None or p.get('sells_1h') is None:risk.append('交易笔数未知')
    if liq<20000:risk.append('流动性薄')
    if sells>buys*1.5:risk.append('卖压偏高')
    if (p.get('change_5m') or 0)>30:risk.append('5m急涨')
    if (p.get('change_5m') or 0)>30:scorev=max(0,scorev-15)
    if safety=='unknown':
        scorev=round(scorev*.7,1); risk.append('可卖性尚未核验；不会触发即时买入提示')
    if safety=='safe': reasons.append('未检出已知硬风险（不保证可卖）')
    risk.extend(p.get('safety_warnings') or [])
    return {'score':scorev,'stage':'hot' if scorev>=70 else 'watch','reasons':reasons,'risk':risk,'buy_ratio':buy_ratio,'age_minutes':age,'safety_status':safety,'safety_blocks':p.get('safety_blocks',[]),'safety_warnings':p.get('safety_warnings',[])}
