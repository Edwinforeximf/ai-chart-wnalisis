// AI Trading Terminal Pro - MT4 Read-only Bridge v4
// Attach on one chart per symbol. NO order placement, modification, or close.
#property strict
input string ApiBase = "https://ai-chart-wnalisis-production.up.railway.app";
input string BridgeToken = "";        // Set in EA inputs. NEVER commit real token.
input int PublishEverySeconds = 30;
int periods[9] = {PERIOD_M1,PERIOD_M5,PERIOD_M15,PERIOD_M30,PERIOD_H1,PERIOD_H4,PERIOD_D1,PERIOD_W1,PERIOD_MN1};
string labels[9] = {"M1","M5","M15","M30","H1","H4","D1","W1","MN1"};

string EscapeJson(string s) {
   StringReplace(s,"\\","\\\\");
   StringReplace(s,"\"","\\\"");
   StringReplace(s,"\r"," ");
   StringReplace(s,"\n"," ");
   return s;
}
string Q(string s) { return "\""+EscapeJson(s)+"\""; }
string N(double d) {
   if(!MathIsValidNumber(d) || d==EMPTY_VALUE) return "null";
   return DoubleToString(d,8);
}
string Int(long i) { return IntegerToString(i); }
string Frame(string sym,int tf,string tfName) {
   if(iBars(sym,tf)<210) return "";
   string candles="";
   int count=0;
   for(int shift=80;shift>=1;shift--) {
      datetime t=iTime(sym,tf,shift);
      if(t<=0) continue;
      if(count>0) candles+=",";
      candles+="{\"time\":"+Int(t)+",\"open\":"+N(iOpen(sym,tf,shift))
           +",\"high\":"+N(iHigh(sym,tf,shift))
           +",\"low\":"+N(iLow(sym,tf,shift))
           +",\"close\":"+N(iClose(sym,tf,shift))+"}";
      count++;
   }
   if(count<30) return "";
   string ind="\"ema20\":"+N(iMA(sym,tf,20,0,MODE_EMA,PRICE_CLOSE,1))
      +",\"ema50\":"+N(iMA(sym,tf,50,0,MODE_EMA,PRICE_CLOSE,1))
      +",\"ema200\":"+N(iMA(sym,tf,200,0,MODE_EMA,PRICE_CLOSE,1))
      +",\"rsi14\":"+N(iRSI(sym,tf,14,PRICE_CLOSE,1))
      +",\"macd_main\":"+N(iMACD(sym,tf,12,26,9,PRICE_CLOSE,MODE_MAIN,1))
      +",\"macd_signal\":"+N(iMACD(sym,tf,12,26,9,PRICE_CLOSE,MODE_SIGNAL,1))
      +",\"adx14\":"+N(iADX(sym,tf,14,PRICE_CLOSE,MODE_MAIN,1))
      +",\"di_plus\":"+N(iADX(sym,tf,14,PRICE_CLOSE,MODE_PLUSDI,1))
      +",\"di_minus\":"+N(iADX(sym,tf,14,PRICE_CLOSE,MODE_MINUSDI,1))
      +",\"atr14\":"+N(iATR(sym,tf,14,1))
      +",\"bb_upper\":"+N(iBands(sym,tf,20,2,0,PRICE_CLOSE,MODE_UPPER,1))
      +",\"bb_lower\":"+N(iBands(sym,tf,20,2,0,PRICE_CLOSE,MODE_LOWER,1));
   return "{\"symbol\":"+Q(sym)+",\"timeframe\":"+Q(tfName)
       +",\"candles\":["+candles+"],\"indicators\":{"+ind+"}}";
}
string PositionsJson() {
   string s=""; int n=0;
   for(int i=OrdersTotal()-1;i>=0 && n<100;i--) {
      if(!OrderSelect(i,SELECT_BY_POS,MODE_TRADES)) continue;
      if(OrderType()!=OP_BUY && OrderType()!=OP_SELL) continue;
      if(n++>0) s+=",";
      s+="{\"ticket\":"+Int(OrderTicket())
       +",\"symbol\":"+Q(OrderSymbol())
       +",\"side\":"+Q(OrderType()==OP_BUY?"BUY":"SELL")
       +",\"volume\":"+N(OrderLots())
       +",\"profit\":"+N(OrderProfit()+OrderSwap()+OrderCommission())+"}";
   }
   return "["+s+"]";
}
string HistoryJson() {
   string s=""; int n=0;
   // MT4 Account History terminal tab controls which historical orders are loaded.
   for(int i=OrdersHistoryTotal()-1;i>=0 && n<40;i--) {
      if(!OrderSelect(i,SELECT_BY_POS,MODE_HISTORY)) continue;
      if(OrderCloseTime()==0 || (OrderType()!=OP_BUY && OrderType()!=OP_SELL)) continue;
      if(n++>0) s+=",";
      s+="{\"ticket\":"+Int(OrderTicket())
       +",\"symbol\":"+Q(OrderSymbol())
       +",\"side\":"+Q(OrderType()==OP_BUY?"BUY":"SELL")
       +",\"volume\":"+N(OrderLots())
       +",\"profit\":"+N(OrderProfit()+OrderSwap()+OrderCommission())
       +",\"closed_at\":"+Int(OrderCloseTime())+"}";
   }
   return "["+s+"]";
}
void PublishSnapshot() {
   string frames=""; int n=0; string sym=Symbol();
   for(int i=0;i<9;i++) {
      string f=Frame(sym,periods[i],labels[i]);
      if(StringLen(f)==0) continue;
      if(n++>0) frames+=",";
      frames+=f;
   }
   if(n==0) { Print("Bridge: waiting for 210 historical bars on a timeframe"); return; }
   string body="{\"platform\":\"MT4\",\"login\":"+Int(AccountNumber())
     +",\"server\":"+Q(AccountServer())+",\"broker\":"+Q(AccountCompany())
     +",\"currency\":"+Q(AccountCurrency())
     +",\"balance\":"+N(AccountBalance())
     +",\"equity\":"+N(AccountEquity())
     +",\"margin\":"+N(AccountMargin())
     +",\"free_margin\":"+N(AccountFreeMargin())
     +",\"positions\":"+PositionsJson()
     +",\"history\":"+HistoryJson()
     +",\"frames\":["+frames+"]}";
   char post[],result[];
   StringToCharArray(body,post,0,WHOLE_ARRAY,CP_UTF8);
   ArrayResize(post,ArraySize(post)-1);
   string responseHeaders="";
   string headers="Content-Type: application/json\r\nX-Bridge-Key: "+BridgeToken+"\r\n";
   ResetLastError();
   int status=WebRequest("POST",ApiBase+"/api/bridge/push",headers,10000,post,result,responseHeaders);
   if(status!=200) Print("Bridge push HTTP=",status," MQL error=",GetLastError());
}
int OnInit() {
   if(StringLen(BridgeToken)<24) { Print("BridgeToken must have at least 24 chars"); return INIT_FAILED; }
   if(StringFind(ApiBase,"https://")!=0) { Print("HTTPS required"); return INIT_FAILED; }
   EventSetTimer(MathMax(10,PublishEverySeconds));
   Print("MT4 bridge read-only; add ",ApiBase," to Tools > Options > Expert Advisors > Allow WebRequest");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() { PublishSnapshot(); }
void OnTick() { /* Never trade */ }
