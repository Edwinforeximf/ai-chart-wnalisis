// AI Trading Terminal Pro v6 — MT5 DEMO ONLY Auto Trader / Cut & Switch
// Requires MT5_IndicatorBridge.mq5 on a separate chart for broker data.
// SAFE DEFAULT: EnableDemoOrders=false. Refuses real accounts and netting mode.
#property strict
#include <Trade/Trade.mqh>
CTrade trade;
input string ApiBase="https://ai-chart-wnalisis-production.up.railway.app";
input string BridgeToken="";
input bool EnableDemoOrders=false;
input int PollEverySeconds=60;
input int EA_Magic=8700625;
input double FixedLot=0.01;
input int MaxSpreadPoints=80;
input double MaxRiskPercent=0.25;
input double MaxDailyDrawdownPercent=1.0;
input int MaxNewTradesPerDay=2;
input int SlippagePoints=30;
input int ReverseCooldownSeconds=120;
input double ATR_StopMultiplier=1.5;
input double ATR_TakeMultiplier=2.0;
int atrHandle=INVALID_HANDLE;

string Prefix(){return "AI6_"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+"_"+_Symbol+"_"+IntegerToString(EA_Magic);}
string Q(string s){StringReplace(s,"\\","\\\\");StringReplace(s,"\"","\\\"");return "\""+s+"\"";}
string DayKey(){MqlDateTime dt;TimeToStruct(TimeCurrent(),dt);return Prefix()+"_D"+IntegerToString(dt.year*10000+dt.mon*100+dt.day);}
bool IsDemo(){return AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO;}
bool RiskBudgetOk() {
   string baseline=DayKey()+"_eq";
   if(!GlobalVariableCheck(baseline))GlobalVariableSet(baseline,AccountInfoDouble(ACCOUNT_EQUITY));
   double first=GlobalVariableGet(baseline);
   return first>0 && AccountInfoDouble(ACCOUNT_EQUITY)>=first*(1.0-MaxDailyDrawdownPercent/100.0);
}
bool CanTradeNow() {
   if(!EnableDemoOrders || !IsDemo() || !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)
      || !MQLInfoInteger(MQL_TRADE_ALLOWED) || !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))return false;
   if(SymbolInfoInteger(_Symbol,SYMBOL_SPREAD)>MaxSpreadPoints)return false;
   return true;
}
bool CanOpenNew() {
   if(!CanTradeNow() || !RiskBudgetOk())return false;
   string k=DayKey()+"_trades";
   if(GlobalVariableCheck(k) && GlobalVariableGet(k)>=MaxNewTradesPerDay)return false;
   return true;
}
bool HasOtherPosition() {
   for(int i=PositionsTotal()-1;i>=0;i--) {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0 || PositionGetString(POSITION_SYMBOL)!=_Symbol)continue;
      if(PositionGetInteger(POSITION_MAGIC)!=EA_Magic)return true;
   }
   return false;
}
long OwnTicket() {
   long ticket=-1;
   for(int i=PositionsTotal()-1;i>=0;i--) {
      ulong id=PositionGetTicket(i);
      if(id==0 || PositionGetString(POSITION_SYMBOL)!=_Symbol)continue;
      if(PositionGetInteger(POSITION_MAGIC)!=EA_Magic)continue;
      if(ticket!=-1)return -2;
      ticket=(long)id;
   }
   return ticket;
}
bool CloseOwned(long ticket) {
   if(ticket<0 || !PositionSelectByTicket((ulong)ticket))return false;
   bool sent=trade.PositionClose((ulong)ticket,SlippagePoints);
   uint result=trade.ResultRetcode();
   bool ok=sent && result==TRADE_RETCODE_DONE && !PositionSelectByTicket((ulong)ticket);
   if(!ok)Print("MT5 Cut not confirmed, retcode=",result);
   return ok;
}
bool RiskForLot(double slDistance) {
   double tickPrice=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tickPrice<=0 || tickValue<=0 || slDistance<=0)return false;
   double estimatedLoss=slDistance/tickPrice*tickValue*FixedLot;
   return estimatedLoss>0 && estimatedLoss<=AccountInfoDouble(ACCOUNT_EQUITY)*MaxRiskPercent/100.0;
}
bool GetPlan(string &side,int &score,long &bar) {
   string body="{\"platform\":\"MT5\",\"login\":"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))
       +",\"server\":"+Q(AccountInfoString(ACCOUNT_SERVER))
       +",\"symbol\":"+Q(_Symbol)+"}";
   char send[],recv[];string responseHeaders="";
   StringToCharArray(body,send,0,WHOLE_ARRAY,CP_UTF8);
   ArrayResize(send,ArraySize(send)-1);
   string headers="Content-Type: application/json\r\nX-Bridge-Key: "+BridgeToken+"\r\n";
   int code=WebRequest("POST",ApiBase+"/api/bridge/strategy",headers,25000,send,recv,responseHeaders);
   if(code!=200)return false;
   string output=CharArrayToString(recv,0,WHOLE_ARRAY,CP_UTF8);
   string parts[];
   int n=StringSplit(output,'|',parts);
   if(n!=4 || parts[0]!="ALLOW")return false;
   if(parts[1]!="BUY" && parts[1]!="SELL")return false;
   side=parts[1];score=(int)StringToInteger(parts[2]);bar=StringToInteger(parts[3]);
   return score>=51 && score<=90 && bar>0;
}
bool OpenDemo(string side,long bar) {
   if(!CanOpenNew() || OwnTicket()!=-1 || HasOtherPosition())return false;
   double minV=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxV=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(FixedLot<minV || FixedLot>maxV || step<=0 ||
      MathAbs(FixedLot/step-MathRound(FixedLot/step))>0.000001)return false;
   double a[];if(CopyBuffer(atrHandle,0,1,1,a)!=1)return false;
   double slD=a[0]*ATR_StopMultiplier,tpD=a[0]*ATR_TakeMultiplier;
   double stopMin=(SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)+5)*_Point;
   if(slD<stopMin || tpD<stopMin || !RiskForLot(slD))return false;
   MqlTick tick;if(!SymbolInfoTick(_Symbol,tick))return false;
   bool buy=side=="BUY";
   double px=buy?tick.ask:tick.bid;
   double sl=NormalizeDouble(px+(buy?-slD:slD),_Digits);
   double tp=NormalizeDouble(px+(buy?tpD:-tpD),_Digits);
   trade.SetExpertMagicNumber(EA_Magic);
   trade.SetDeviationInPoints(SlippagePoints);
   bool sent=buy?trade.Buy(FixedLot,_Symbol,px,sl,tp,"AI6-DEMO")
                :trade.Sell(FixedLot,_Symbol,px,sl,tp,"AI6-DEMO");
   uint rc=trade.ResultRetcode();
   if(!sent || rc!=TRADE_RETCODE_DONE) {
      Print("MT5 entry not confirmed, retcode=",rc);
      return false;
   }
   GlobalVariableSet(Prefix()+"_bar",(double)bar);
   string k=DayKey()+"_trades";
   GlobalVariableSet(k,(GlobalVariableCheck(k)?GlobalVariableGet(k):0)+1);
   Print("MT5 DEMO "+side+" trade executed, deal=",trade.ResultDeal());
   return true;
}
int OnInit() {
   if(!IsDemo()) {Print("DEMO ONLY. No real-account trading.");return INIT_FAILED;}
   // On netting accounts manual and EA positions can mix. Only hedging demo supported.
   if(AccountInfoInteger(ACCOUNT_MARGIN_MODE)!=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING) {
      Print("For safety this demo EA requires a hedging account.");return INIT_FAILED;
   }
   if(StringFind(ApiBase,"https://")!=0 || StringLen(BridgeToken)<24 ||
      FixedLot<=0 || MaxRiskPercent<=0 || MaxRiskPercent>1 ||
      MaxDailyDrawdownPercent<=0 || MaxDailyDrawdownPercent>3 ||
      MaxNewTradesPerDay<1 || MaxNewTradesPerDay>5 ||
      ReverseCooldownSeconds<60)return INIT_FAILED;
   atrHandle=iATR(_Symbol,PERIOD_H1,14);
   if(atrHandle==INVALID_HANDLE)return INIT_FAILED;
   trade.SetExpertMagicNumber(EA_Magic);
   trade.SetTypeFillingBySymbol(_Symbol);
   EventSetTimer(MathMax(30,PollEverySeconds));
   Print("MT5 demo cut-switch loaded. EnableDemoOrders=",EnableDemoOrders);
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) {EventKillTimer();if(atrHandle!=INVALID_HANDLE)IndicatorRelease(atrHandle);}
void OnTick() { /* Timer-only to avoid duplicate entries */ }
void OnTimer() {
   if(!CanTradeNow())return;
   string side="";int score=0;long bar=0;
   if(!GetPlan(side,score,bar))return;
   if(GlobalVariableCheck(Prefix()+"_bar") && (long)GlobalVariableGet(Prefix()+"_bar")==bar)return;
   if(HasOtherPosition())return;
   long ticket=OwnTicket();if(ticket==-2)return;
   if(ticket>=0) {
      if(!PositionSelectByTicket((ulong)ticket))return;
      ENUM_POSITION_TYPE current=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      if((current==POSITION_TYPE_BUY&&side=="BUY")||(current==POSITION_TYPE_SELL&&side=="SELL"))return;
      if(!CloseOwned(ticket))return;
      GlobalVariableSet(Prefix()+"_reverse",TimeCurrent()+ReverseCooldownSeconds);
      Print("DEMO cut complete. Re-check signal after cooldown.");
      return;
   }
   if(GlobalVariableCheck(Prefix()+"_reverse")&&
      TimeCurrent()<GlobalVariableGet(Prefix()+"_reverse"))return;
   OpenDemo(side,bar);
}
