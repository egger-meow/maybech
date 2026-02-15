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
