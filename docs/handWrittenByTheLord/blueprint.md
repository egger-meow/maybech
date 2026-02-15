this project should be as clean and human readable as writable as possible, any redundent elements and codes should be removed, and the code should be as modular as possible 

the trader focus on two things instant momentum and 回測
for instant, current 15m candle has 10 times higher volume than the previous 15m candle, and the price is going up, then it is a instant momentum, we should long it instanly and short it if the price is going down. something like this.

and before any strategy is put to the real running, it should pass the 回測 of a peroid of previous time from now, only if the 收益率 勝率 passing a threshold, then it would be put to the real running, and when the real running is going on, it should also be monitored, if the 收益率 勝率 is decreasing, then it should be stopped and put back to the 回測 to check again and logging to the log.

there should be a python module that 回測 any strategy, and be visualy clear for user to see the result, like which times trigger and results of evert trigger times recently...

we use OKX api to do the project and run on local machine with python
AMD Ryzen 7 8700F 8-Core Processor
32gb ram
windows 11
Radeon RX 9060xt 16g

and i hope it can finally run stablly on the backgroud with information sending for me to track with my phone maybe linebot(but i have no experience on it) or through email, 

by the way also be ablt to montor current account balance and positions

now I have come up with a clear initial strategy that focus on 1m candle and your implementation should fulling align to this and try to fit this into the system.

when the current growing 1m candle(maybe check every 5 sec) volume is larger than k times(k倍) of the previous canles(k depends on we are going to long or short, if long k maybe larger, currenly my idea is 10, and when short k may be like 5, but these parameters i think can be determined through backtesting), and also the price gap from current to the end of the last candle should also larger than a threshould(currently for eth i will take price gap as 3 or 4), than the signal trigers, the order will be place as 限價單!!!at the price detected, and the stoploss would be at the end of the last candle(we set it E, and current detected price as C, and their distance as D), and stop win at C+D(if C > E and we are going to long), and for short it would be C-D(if C < E and we are going to short), and the position size should be calculated based on the account balance and the stoploss distance, and the max position ratio, but now for easyness we are going to do it for eth and poistion size is fixed as 0.1eth. And for backtesting there is a bug which we can not mimic the behavior that we check every 5 sec since we can only get all 1min candles. So we going to do it conservativly, when we see two consecutive 1m candles match the volumes condition and the gap condition we are going to test would be the distance between the close of the first candle and the close of the second candle, and the stoploss would be the close of the first candle, and the stop win would be the close of the second candle plus the distance between the close of the first candle and the close of the second candle, and the next or maybe a future candle may reach two prices(stop win, stop loss) in the same 1min candle and we wont know which price is reached first, in this case we would assume it reach stop loss first, only when the that future candle reach to only stopwin(range cover) first without touching stoploss(and the candles between it and order placed candle never touch stop loss), then we would count it as a success, otherwise it is a failure. and the backtesting would focus on winrate rather than the accurate profit since the transaction fee is now not considered. above would be the main strategy we are focus on currently, If anything not clear enough you should ask me, and you should express yourself what u understand above to me