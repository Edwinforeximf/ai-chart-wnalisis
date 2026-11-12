// AI Trading Terminal Pro - MT5 Read-only Bridge v4
// Attach to one chart per symbol. NO trading or position-close code.
#property strict
input string ApiBase = "https://ai-chart-wnalisis-production.up.railway.app";
input string BridgeToken = "";     // Enter in EA Inputs; NEVER commit credentials.
input int PublishEverySeconds = 30;
ENUM_TIMEFRAMES periods[9]={PERIOD_M1,PERIOD_M5,PERIOD_M15,PERIOD_M30,PERIOD_H1,PERIOD_H4,PERIOD_D1,PERIOD_W1,PERIOD_MN1};
string labels[9]={"M1","M5","M15","M30","H1","H4","D1","W1","MN1"};
int ema20[9],ema50[9],ema200[9],rsi[9],macd[9],adx[9],atr[9],bands[9];
string EscapeJson(string s) {
   StringReplace(s,"\\","\\\\");
   StringReplace(s,"\"","\\\"");
   StringReplace(s,"\r"," ");
   StringReplace(s,"\n"," ");
   return s;
}
string Q(string s) {return "\""+EscapeJson(s)+"\"";}
string N(double d) {
   if(!MathIsValidNumber(d) || d==EMPTY_VALUE) return "null";
   return DoubleToString(d,8);
}
string Int(long n) {return IntegerToString(n);}
double ReadBuffer(int handle,int index) {
   double values[];
   if(handle==INVALID_HANDLE || CopyBuffer(handle,index,1,1,values)!=1) return EMPTY_VALUE;
   return values[0]; // bar shift 1 = latest CLOSED bar
}
string Frame(string sym,int i) {
   MqlRates rates[];
   int total=CopyRates(sym,periods[i],1,80,rates);
   if(total<30) return "";
   string candles="";
   // CopyRates places oldest element at physical index 0 in an ordinary array.
   for(int j=0;j<total;j++) {
      if(j>0) candles+=",";
      candles+="{\"time\":"+Int(rates[j].time)
       +",\"open\":"+N(rates[j].open)+",\"high\":"+N(rates[j].high)
       +",\"low\":"+N(rates[j].low)+",\"close\":"+N(rates[j].close)+"}";
   }
   string ind="\"ema20\":"+N(ReadBuffer(ema20[i],0))
       +",\"ema50\":"+N(ReadBuffer(ema50[i],0))
       +",\"ema200\":"+N(ReadBuffer(ema200[i],0))
       +",\"rsi14\":"+N(ReadBuffer(rsi[i],0))
       +",\"macd_main\":"+N(ReadBuffer(macd[i],0))
       +",\"macd_signal\":"+N(ReadBuffer(macd[i],1))
       +",\"adx14\":"+N(ReadBuffer(adx[i],0))
       +",\"di_plus\":"+N(ReadBuffer(adx[i],1))
       +",\"di_minus\":"+N(ReadBuffer(adx[i],2))
       +",\"atr14\":"+N(ReadBuffer(atr[i],0))
       +",\"bb_upper\":"+N(ReadBuffer(bands[i],1))
       +",\"bb_lower\":"+N(ReadBuffer(bands[i],2));
   return "{\"symbol\":"+Q(sym)+",\"timeframe\":"+Q(labels[i])
         +",\"candles\":["+candles+"],\"indicators\":{"+ind+"}}";
}
string PositionsJson() {
   string s="";int n=0;
   for(int i=PositionsTotal()-1;i>=0 && n<100;i--) {
      ulong ticket=PositionGetTicket(i);
      if(ticket==0) continue; // PositionGetTicket also selects the position
      if(n++>0) s+=",";
      s+="{\"ticket\":"+Int((long)ticket)
       +",\"symbol\":"+Q(PositionGetString(POSITION_SYMBOL))
       +",\"side\":"+Q(PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY?"BUY":"SELL")
       +",\"volume\":"+N(PositionGetDouble(POSITION_VOLUME))
       +",\"profit\":"+N(PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP))+"}";
   }
   return "["+s+"]";
}
string HistoryJson() {
   string s="";int n=0;
   if(!HistorySelect(TimeCurrent()-90*86400,TimeCurrent())) return "[]";
   for(int i=HistoryDealsTotal()-1;i>=0 && n<40;i--) {
      ulong ticket=HistoryDealGetTicket(i);
      if(ticket==0) continue;
      ENUM_DEAL_ENTRY entry=(ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket,DEAL_ENTRY);
      if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY) continue;
      ENUM_DEAL_TYPE ty=(ENUM_DEAL_TYPE)HistoryDealGetInteger(ticket,DEAL_TYPE);
      if(ty!=DEAL_TYPE_BUY && ty!=DEAL_TYPE_SELL) continue;
      if(n++>0) s+=",";
      s+="{\"ticket\":"+Int((long)ticket)
       +",\"symbol\":"+Q(HistoryDealGetString(ticket,DEAL_SYMBOL))
       +",\"side\":"+Q(ty==DEAL_TYPE_SELL?"BUY":"SELL") // position's direction
       +",\"volume\":"+N(HistoryDealGetDouble(ticket,DEAL_VOLUME))
       +",\"profit\":"+N(HistoryDealGetDouble(ticket,DEAL_PROFIT)
           +HistoryDealGetDouble(ticket,DEAL_SWAP)
           +HistoryDealGetDouble(ticket,DEAL_COMMISSION))
       +",\"closed_at\":"+Int(HistoryDealGetInteger(ticket,DEAL_TIME))+"}";
   }
   return "["+s+"]";
}
void PublishSnapshot() {
   string frames="";int n=0;string sym=_Symbol;
   for(int i=0;i<9;i++) {
      string f=Frame(sym,i);
      if(StringLen(f)==0) continue;
      if(n++>0) frames+=",";
      frames+=f;
   }
   if(n==0) {Print("Bridge: waiting for closed candle history");return;}
   string body="{\"platform\":\"MT5\",\"login\":"+Int(AccountInfoInteger(ACCOUNT_LOGIN))
     +",\"server\":"+Q(AccountInfoString(ACCOUNT_SERVER))
     +",\"broker\":"+Q(AccountInfoString(ACCOUNT_COMPANY))
     +",\"currency\":"+Q(AccountInfoString(ACCOUNT_CURRENCY))
     +",\"balance\":"+N(AccountInfoDouble(ACCOUNT_BALANCE))
     +",\"equity\":"+N(AccountInfoDouble(ACCOUNT_EQUITY))
     +",\"margin\":"+N(AccountInfoDouble(ACCOUNT_MARGIN))
     +",\"free_margin\":"+N(AccountInfoDouble(ACCOUNT_MARGIN_FREE))
     +",\"positions\":"+PositionsJson()+",\"history\":"+HistoryJson()
     +",\"frames\":["+frames+"]}";
   char post[],response[];
   StringToCharArray(body,post,0,WHOLE_ARRAY,CP_UTF8);
   ArrayResize(post,ArraySize(post)-1);
   string responseHeaders="";
   string headers="Content-Type: application/json\r\nX-Bridge-Key: "+BridgeToken+"\r\n";
   ResetLastError();
   int code=WebRequest("POST",ApiBase+"/api/bridge/push",headers,10000,post,response,responseHeaders);
   if(code!=200) Print("Bridge push HTTP=",code," MQL error=",GetLastError());
}
int OnInit() {
   if(StringLen(BridgeToken)<24 || StringFind(ApiBase,"https://")!=0) {
      Print("Requires HTTPS ApiBase and BridgeToken (min 24 characters)");
      return INIT_FAILED;
   }
   for(int i=0;i<9;i++) {
      ema20[i]=iMA(_Symbol,periods[i],20,0,MODE_EMA,PRICE_CLOSE);
      ema50[i]=iMA(_Symbol,periods[i],50,0,MODE_EMA,PRICE_CLOSE);
      ema200[i]=iMA(_Symbol,periods[i],200,0,MODE_EMA,PRICE_CLOSE);
      rsi[i]=iRSI(_Symbol,periods[i],14,PRICE_CLOSE);
      macd[i]=iMACD(_Symbol,periods[i],12,26,9,PRICE_CLOSE);
      adx[i]=iADX(_Symbol,periods[i],14);
      atr[i]=iATR(_Symbol,periods[i],14);
      bands[i]=iBands(_Symbol,periods[i],20,0,2.0,PRICE_CLOSE);
   }
   EventSetTimer(MathMax(10,PublishEverySeconds));
   Print("MT5 bridge read-only. Allow WebRequest to ",ApiBase," in Expert Advisors Options.");
   return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) {
   EventKillTimer();
   for(int i=0;i<9;i++) {
      IndicatorRelease(ema20[i]);IndicatorRelease(ema50[i]);IndicatorRelease(ema200[i]);
      IndicatorRelease(rsi[i]);IndicatorRelease(macd[i]);IndicatorRelease(adx[i]);
      IndicatorRelease(atr[i]);IndicatorRelease(bands[i]);
   }
}
void OnTimer() {PublishSnapshot();}
void OnTick() { /* No trading */ }
