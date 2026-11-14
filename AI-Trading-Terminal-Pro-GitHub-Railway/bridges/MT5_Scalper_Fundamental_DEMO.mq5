// AI Trading Terminal Pro v7 — MT5 DEMO ONLY Fundamental Scalper / Cut & Switch
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
input double MaxLot=0.05;              // Cap, not mandatory lot
input double RiskPercent=0.25;         // Lot derived from stop distance and account equity
input double MaxSpreadToStop=0.20;
input int MaxSpreadPoints=80;
input double MaxRiskPercent=0.25;
input double MaxDailyDrawdownPercent=1.0;
input int MaxNewTradesPerDay=3;
input int SlippagePoints=30;
input int ReverseCooldownSeconds=180;
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
double DynamicLot(double px,double stop,bool buy) {
   double minV=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxV=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(minV<=0 || step<=0 || stop<=0 || MaxLot<minV)return 0;
   double slPrice=px+(buy?-stop:stop);
   double lossPerLot=0.0;
   ENUM_ORDER_TYPE type=buy?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
   if(!OrderCalcProfit(type,_Symbol,1.0,px,slPrice,lossPerLot))return 0;
   if(lossPerLot>=0 || !MathIsValidNumber(lossPerLot))return 0;
   double budget=AccountInfoDouble(ACCOUNT_EQUITY)*MathMin(RiskPercent,MaxRiskPercent)/100.0;
   double raw=budget/(MathAbs(lossPerLot)*1.2);
   double lots=NormalizeDouble(MathFloor(MathMin(raw,MathMin(MaxLot,maxV))/step)*step,3);
   if(lots<minV)return 0; // broker minimum would exceed risk budget
   double requiredMargin=0;
   if(!OrderCalcMargin(type,_Symbol,lots,px,requiredMargin))return 0;
   if(requiredMargin<=0 || AccountInfoDouble(ACCOUNT_MARGIN_FREE)-requiredMargin
      <AccountInfoDouble(ACCOUNT_EQUITY)*0.25)return 0;
   if(MathAbs(lossPerLot)*lots*1.2>budget)return 0;
   return lots;
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
   if(!CanOpenNew() || OwnTicket()!=-1 || HasOtherPosition() || PositionsTotal()>0)return false;
   double a[];if(CopyBuffer(atrHandle,0,1,1,a)!=1)return false;
   double slD=a[0]*ATR_StopMultiplier,tpD=a[0]*ATR_TakeMultiplier;
   double stopMin=(SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)+5)*_Point;
   if(slD<stopMin || tpD<stopMin || SymbolInfoInteger(_Symbol,SYMBOL_SPREAD)*_Point>slD*MaxSpreadToStop)return false;
   MqlTick tick;if(!SymbolInfoTick(_Symbol,tick))return false;
   bool buy=side=="BUY";
   double px=buy?tick.ask:tick.bid;
   double lots=DynamicLot(px,slD,buy);
   if(lots<=0)return false;
   double sl=NormalizeDouble(px+(buy?-slD:slD),_Digits);
   double tp=NormalizeDouble(px+(buy?tpD:-tpD),_Digits);
   trade.SetExpertMagicNumber(EA_Magic);
   trade.SetDeviationInPoints(SlippagePoints);
   bool sent=buy?trade.Buy(lots,_Symbol,px,sl,tp,"AI7-SCALP-DEMO")
                :trade.Sell(lots,_Symbol,px,sl,tp,"AI7-SCALP-DEMO");
   uint rc=trade.ResultRetcode();
   if(!sent || rc!=TRADE_RETCODE_DONE) {
      Print("MT5 entry not confirmed, retcode=",rc);
      return false;
   }
   GlobalVariableSet(Prefix()+"_bar",(double)bar);
   string k=DayKey()+"_trades";
   GlobalVariableSet(k,(GlobalVariableCheck(k)?GlobalVariableGet(k):0)+1);
   Print("MT5 DEMO scalp "+side+" lots=",lots," deal=",trade.ResultDeal());
   return true;
}
int OnInit() {
   if(!IsDemo()) {Print("DEMO ONLY. No real-account trading.");return INIT_FAILED;}
   // On netting accounts manual and EA positions can mix. Only hedging demo supported.
   if(AccountInfoInteger(ACCOUNT_MARGIN_MODE)!=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING) {
      Print("For safety this demo EA requires a hedging account.");return INIT_FAILED;
   }
   if(StringFind(ApiBase,"https://")!=0 || StringLen(BridgeToken)<24 ||
      MaxLot<=0 || RiskPercent<=0 || RiskPercent>MaxRiskPercent || MaxSpreadToStop<=0 || MaxSpreadToStop>0.5 || MaxRiskPercent<=0 || MaxRiskPercent>1 ||
      MaxDailyDrawdownPercent<=0 || MaxDailyDrawdownPercent>3 ||
      MaxNewTradesPerDay<1 || MaxNewTradesPerDay>5 ||
      ReverseCooldownSeconds<60)return INIT_FAILED;
   atrHandle=iATR(_Symbol,PERIOD_M5,14);
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
