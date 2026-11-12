from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from fastapi import Query
from pathlib import Path
from datetime import datetime, timezone
import random, uuid

app = FastAPI(title='AI Trading Terminal Pro - Demo MVP')

# Optional read-only broker telemetry; requires Railway environment tokens.
from live_bridge import router as live_router
app.include_router(live_router)
BASE = Path(__file__).parent
random.seed(14)
SYMBOLS = ['XAUUSD','XAGUSD','EURUSD','GBPUSD','USDJPY','USOIL']
prices = {'XAUUSD':2700.0,'XAGUSD':32.0,'EURUSD':1.08,'GBPUSD':1.29,'USDJPY':150.0,'USOIL':72.0}
orders = []

class Approval(BaseModel):
    signal_id: str
    approved: bool

signals = {}

TIMEFRAMES = {
    'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800,
    'H1': 3600, 'H4': 14400, 'D1': 86400, 'W1': 604800, 'MN1': 2592000,
}
# Deterministic historical sample data for UI development only.
# All timeframes use independent illustrative candles, not broker prices.

def candles(symbol, timeframe='H1'):
    if symbol not in SYMBOLS:
        raise HTTPException(404, 'Unknown symbol')
    if timeframe not in TIMEFRAMES:
        raise HTTPException(422, 'Unsupported timeframe')
    seconds=TIMEFRAMES[timeframe]
    rng=random.Random(f'{symbol}:{timeframe}:demo-v3')
    base=prices[symbol]
    scale=base*({'M1':.00018,'M5':.00034,'M15':.00048,'M30':.0007,
                'H1':.001,'H4':.002,'D1':.004,'W1':.008,'MN1':.013}[timeframe])
    end_at=int(datetime(2026,11,1,tzinfo=timezone.utc).timestamp())
    end_at-=end_at % seconds
    rows=[]
    for i in range(180):
        o=base
        change=rng.uniform(-scale,scale)
        c=max(.00001,o+change)
        h=max(o,c)+rng.uniform(0,scale*.5)
        l=max(.00001,min(o,c)-rng.uniform(0,scale*.5))
        rows.append({'time':end_at-(179-i)*seconds,
                     'open':round(o,5),'high':round(h,5),
                     'low':round(l,5),'close':round(c,5)})
        base=c
    return rows

def technical_signal(symbol, timeframe):
    rows=candles(symbol,timeframe)
    closes=[r['close'] for r in rows]
    fast=sum(closes[-10:])/10
    slow=sum(closes[-30:])/30
    direction='BUY' if fast>slow else 'SELL'
    strength=min(35,abs(fast-slow)/max(abs(slow),1e-8)*10000)
    return {'timeframe':timeframe,'side':direction,
            'score':round(50+strength,1),
            'technical':{'sma10':round(fast,5),'sma30':round(slow,5)}}

@app.get('/', response_class=HTMLResponse)
def home(): return (BASE/'index.html').read_text(encoding='utf-8')

@app.get('/api/status')
def status(): return {'mode':'DEMO','broker':'DISCONNECTED','execution_enabled':False,'data':'SYNTHETIC','fundamentals':'NOT_CONNECTED'}

@app.get('/api/timeframes')
def list_timeframes():
    return {'timeframes':list(TIMEFRAMES.keys()),'mode':'DEMO'}

@app.get('/api/candles/{symbol}')
def get_candles(symbol:str, timeframe:str='H1'):
    return {'symbol':symbol,'timeframe':timeframe,'source':'SYNTHETIC_DEMO',
            'candles':candles(symbol,timeframe)}

@app.post('/api/analyze/{symbol}')
def analyze(symbol:str, timeframe:str='H1'):
    result=technical_signal(symbol,timeframe)
    sid=str(uuid.uuid4())
    result.update({'id':sid,'symbol':symbol,'score_type':'HEURISTIC_NOT_PROBABILITY',
                   'fundamental':'UNAVAILABLE','state':'PENDING_APPROVAL',
                   'data_source':'SYNTHETIC_DEMO',
                   'disclaimer':'No live market data. No broker order will be placed.'})
    signals[sid]=result
    return result

@app.get('/api/analyze-multi/{symbol}')
def analyze_multi(symbol:str):
    frames=['M15','H1','H4','D1']
    results=[technical_signal(symbol,tf) for tf in frames]
    buy=sum(r['side']=='BUY' for r in results)
    sell=len(results)-buy
    consensus='BUY' if buy>=3 else 'SELL' if sell>=3 else 'MIXED'
    return {'symbol':symbol,'timeframes':results,'consensus':consensus,
            'buy_count':buy,'sell_count':sell,
            'source':'SYNTHETIC_DEMO',
            'fundamental':'UNAVAILABLE',
            'note':'Demo SMA alignment only. Not predictive confidence, not an executable signal.'}

@app.post('/api/approve')
def approve(body:Approval):
    signal=signals.get(body.signal_id)
    if not signal: raise HTTPException(404,'Signal not found')
    if signal['state']!='PENDING_APPROVAL': raise HTTPException(409,'Already decided')
    signal['state']='APPROVED_DEMO' if body.approved else 'REJECTED'
    if body.approved:
        orders.append({'id':str(uuid.uuid4()),'symbol':signal['symbol'],'side':signal['side'],'status':'SIMULATED_ONLY','signal_id':signal['id']})
    return {'state':signal['state'],'broker_order_placed':False}

@app.get('/api/orders')
def list_orders(): return orders


# DEMO-only account endpoints. No real credentials, trading or broker connection.
DEMO_ACCOUNTS = {
    "mt5-demo": {"id":"mt5-demo","platform":"MT5","broker":"Example Broker A (simulated)","currency":"USD","balance":10000.0,"margin":350.0},
    "mt4-demo": {"id":"mt4-demo","platform":"MT4","broker":"Example Broker B (simulated)","currency":"USD","balance":5000.0,"margin":120.0},
}
DEMO_POSITIONS = {
    "mt5-demo": [{"ticket":1001,"symbol":"XAUUSD","side":"BUY","volume":0.10,"profit":84.5},{"ticket":1002,"symbol":"EURUSD","side":"SELL","volume":0.20,"profit":-12.25}],
    "mt4-demo": [{"ticket":2001,"symbol":"GBPUSD","side":"BUY","volume":0.10,"profit":23.75}],
}
DEMO_HISTORY = {k:[] for k in DEMO_ACCOUNTS}

@app.get('/api/accounts')
def list_accounts():
    return {"mode":"DEMO","accounts":list(DEMO_ACCOUNTS.values())}

@app.get('/api/accounts/{account_id}/summary')
def account_summary(account_id:str):
    account=DEMO_ACCOUNTS.get(account_id)
    if not account: raise HTTPException(404,'Demo account not found')
    floating=round(sum(p['profit'] for p in DEMO_POSITIONS[account_id]),2)
    equity=round(account['balance']+floating,2)
    return {**account,"equity":equity,"floating_profit":floating,"free_margin":round(equity-account['margin'],2),"open_positions":len(DEMO_POSITIONS[account_id]),"source":"SYNTHETIC_DEMO"}

@app.get('/api/accounts/{account_id}/positions')
def account_positions(account_id:str):
    if account_id not in DEMO_ACCOUNTS: raise HTTPException(404,'Demo account not found')
    return {"mode":"DEMO","positions":DEMO_POSITIONS[account_id]}

@app.get('/api/accounts/{account_id}/history')
def account_history(account_id:str):
    if account_id not in DEMO_ACCOUNTS: raise HTTPException(404,'Demo account not found')
    return {"mode":"DEMO","history":DEMO_HISTORY[account_id]}

class DemoCloseRequest(BaseModel):
    confirm_demo: bool = False
    side: str = 'ALL'

@app.post('/api/accounts/{account_id}/close-all-demo')
def close_all_demo(account_id:str, body:DemoCloseRequest):
    if account_id not in DEMO_ACCOUNTS: raise HTTPException(404,'Demo account not found')
    if not body.confirm_demo: raise HTTPException(400,'Explicit demo confirmation required')
    if body.side not in ('ALL','BUY','SELL'): raise HTTPException(400,'Invalid side')
    selected=[p for p in DEMO_POSITIONS[account_id] if body.side=='ALL' or p['side']==body.side]
    DEMO_POSITIONS[account_id]=[p for p in DEMO_POSITIONS[account_id] if p not in selected]
    for p in selected:
        DEMO_HISTORY[account_id].append({**p,'closed_at':datetime.now(timezone.utc).isoformat(),'status':'SIMULATED_CLOSE'})
    return {"mode":"DEMO","broker_orders_sent":0,"simulated_closed_count":len(selected),"history":DEMO_HISTORY[account_id]}
