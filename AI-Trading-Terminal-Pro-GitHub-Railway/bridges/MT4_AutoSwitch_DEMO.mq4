// AI Trading Terminal Pro v6 — MT4 DEMO ONLY Auto Trader / Cut & Switch
// IMPORTANT: requires separate read-only MT4 IndicatorBridge EA feeding Railway.
// Attach ONCE per chart symbol, to a second chart of the same symbol.
// NEVER runs on a real-money account. Demo trading OFF until explicit opt-in.
#property strict
input string ApiBase = "https://ai-chart-wnalisis-production.up.railway.app";
input string BridgeToken = "";           // same as Railway BRIDGE_TOKEN
input bool EnableDemoOrders = false;     // SAFE DEFAULT: no orders
input int PollEverySeconds = 60;
input int EA_Magic = 8700624;
input double FixedLot = 0.01;
input int MaxSpreadPoints = 80;
input double MaxRiskPercent = 0.25;
input double MaxDailyDrawdownPercent = 1.0;
input int MaxNewTradesPerDay = 2;
input int SlippagePoints = 30;
input int ReverseCooldownSeconds = 120;
input double ATR_StopMultiplier = 1.5;
input double ATR_TakeMultiplier = 2.0;

string Prefix() {return "AI6_"+IntegerToString(AccountNumber())+"_"+Symbol()+"_"+IntegerToString(EA_Magic);}
string Q(string s) {StringReplace(s,"\\","\\\\");StringReplace(s,"\"","\\\"");return "\""+s+"\"";}
bool IsDemo() {return AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO;}
string DayKey() {datetime t=TimeCurrent();return Prefix()+"_D"+IntegerToString(TimeYear(t)*10000+TimeMonth(t)*100+TimeDay(t));}
bool Permitted() {
   if(!EnableDemoOrders || !IsDemo() || !IsTradeAllowed()) return false;
   if(MarketInfo(Symbol(),MODE_SPREAD)>MaxSpreadPoints) return false;
   string k=DayKey()+"_eq";
   if(!GlobalVariableCheck(k)) GlobalVariableSet(k,AccountEquity());
   double startEq=GlobalVariableGet(k);
   if(startEq<=0 || AccountEquity() < startEq*(1.0-MaxDailyDrawdownPercent/100.0)) return false;
   string tradeK=DayKey()+"_trades";
   if(GlobalVariableCheck(tradeK) && GlobalVariableGet(tradeK)>=MaxNewTradesPerDay)return false;
   return true;
}
bool ExternalPositionExists() {
   for(int j=OrdersTotal()-1;j>=0;j--) {
      if(!OrderSelect(j,SELECT_BY_POS,MODE_TRADES))continue;
      if(OrderSymbol()!=Symbol())continue;
      if((OrderType()==OP_BUY||OrderType()==OP_SELL) && OrderMagicNumber()!=EA_Magic)return true;
   }
   return false;
}
int OwnTicket() {
   int first=-1;
   for(int j=OrdersTotal()-1;j>=0;j--) {
      if(!OrderSelect(j,SELECT_BY_POS,MODE_TRADES))continue;
      if(OrderSymbol()!=Symbol() || OrderMagicNumber()!=EA_Magic)continue;
      if(OrderType()!=OP_BUY && OrderType()!=OP_SELL)continue;
      if(first!=-1)return -2; // unexpected multiple EA positions: stop
      first=OrderTicket();
   }
   return first;
}
bool RiskForLot(double stopDistance) {
   double tickSizePts=MarketInfo(Symbol(),MODE_TICKSIZE);
   double tickValue=MarketInfo(Symbol(),MODE_TICKVALUE);
   if(tickSizePts<=0 || tickValue<=0 || stopDistance<=0)return false;
   double tickPrice=tickSizePts*Point;
   double loss=stopDistance/tickPrice*tickValue*FixedLot;
   return loss>0 && loss<=AccountEquity()*MaxRiskPercent/100.0;
}
bool CloseOwned(int ticket) {
   if(!OrderSelect(ticket,SELECT_BY_TICKET) || OrderCloseTime()!=0)return false;
   RefreshRates();
   int side=OrderType();
   double price=(side==OP_BUY)?Bid:Ask;
   bool ok=OrderClose(ticket,OrderLots(),NormalizeDouble(price,Digits),SlippagePoints,clrNONE);
   if(!ok)Print("Cut failed. Error ",GetLastError());
   return ok;
}
bool OpenDemo(string side,long bar) {
   if(!Permitted() || ExternalPositionExists() || OwnTicket()!=-1)return false;
   if(FixedLot<MarketInfo(Symbol(),MODE_MINLOT) || FixedLot>MarketInfo(Symbol(),MODE_MAXLOT))return false;
   double step=MarketInfo(Symbol(),MODE_LOTSTEP);
   if(step<=0 || MathAbs(FixedLot/step-MathRound(FixedLot/step))>0.000001)return false;
   double atr=iATR(Symbol(),PERIOD_H1,14,1);
   double stop=atr*ATR_StopMultiplier, take=atr*ATR_TakeMultiplier;
   double minimum=(MarketInfo(Symbol(),MODE_STOPLEVEL)+5)*Point;
   if(atr<=0 || stop<minimum || take<minimum || !RiskForLot(stop))return false;
   RefreshRates();
   bool buy=side=="BUY";
   double price=buy?Ask:Bid;
   double sl=NormalizeDouble(price+(buy?-stop:stop),Digits);
   double tp=NormalizeDouble(price+(buy?take:-take),Digits);
   int ticket=OrderSend(Symbol(),buy?OP_BUY:OP_SELL,FixedLot,
                        NormalizeDouble(price,Digits),SlippagePoints,sl,tp,
                        "AI6-DEMO",EA_Magic,0,clrNONE);
   if(ticket<0) {Print("Entry rejected by broker: ",GetLastError());return false;}
   GlobalVariableSet(Prefix()+"_bar",(double)bar);
   string k=DayKey()+"_trades";
   GlobalVariableSet(k,(GlobalVariableCheck(k)?GlobalVariableGet(k):0)+1);
   Print("DEMO "+side+" opened, ticket ",ticket," SL ",sl," TP ",tp);
   return true;
}
bool GetPlan(string &side,int &score,long &bar) {
   string body="{\"platform\":\"MT4\",\"login\":"+IntegerToString(AccountNumber())
       +",\"server\":"+Q(AccountServer())+",\"symbol\":"+Q(Symbol())+"}";
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
int OnInit() {
   if(StringFind(ApiBase,"https://")!=0 || StringLen(BridgeToken)<24)return INIT_FAILED;
   if(!IsDemo()){Print("DEMO ONLY EA refuses real accounts.");return INIT_FAILED;}
   if(FixedLot<=0 || MaxRiskPercent<=0 || MaxRiskPercent>1 ||
      MaxDailyDrawdownPercent<=0 || MaxDailyDrawdownPercent>3 ||
      MaxNewTradesPerDay<1 || MaxNewTradesPerDay>5 ||
      ReverseCooldownSeconds<60)return INIT_FAILED;
   EventSetTimer(MathMax(30,PollEverySeconds));
   Print("AI Auto EA MT4 loaded, EnableDemoOrders=",EnableDemoOrders,
         ". Must allow WebRequest for ",ApiBase);
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason){EventKillTimer();}
void OnTick(){/* Timer-based to avoid repeated orders on ticks. */}
void OnTimer() {
   // Always permit evaluation of a protective cut, even when daily new-entry limits are reached.
   if(!EnableDemoOrders || !IsDemo() || !IsTradeAllowed())return;
   string side="";int score=0;long bar=0;
   if(!GetPlan(side,score,bar))return;
   if(GlobalVariableCheck(Prefix()+"_bar") && (long)GlobalVariableGet(Prefix()+"_bar")==bar)return;
   if(ExternalPositionExists())return;
   int ticket=OwnTicket();
   if(ticket==-2)return;
   if(ticket>=0) {
      if(!OrderSelect(ticket,SELECT_BY_TICKET))return;
      int positionSide=OrderType();
      if((positionSide==OP_BUY&&side=="BUY")||(positionSide==OP_SELL&&side=="SELL"))return;
      if(!CloseOwned(ticket))return;
      GlobalVariableSet(Prefix()+"_reverse",TimeCurrent()+ReverseCooldownSeconds);
      Print("Cut completed. Reverse requires a fresh qualified check after cooldown.");
      return; // never reverse in same callback
   }
   if(GlobalVariableCheck(Prefix()+"_reverse") &&
      TimeCurrent()<GlobalVariableGet(Prefix()+"_reverse"))return;
   OpenDemo(side,bar);
}
