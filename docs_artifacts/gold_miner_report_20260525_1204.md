# Gold Miner Report v1

```
run_timestamp_utc  : 2026-05-25T12:04:48.434497+00:00
since_hours        : 48.0
symbols            : BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
windows_seconds    : 300, 900, 1800
total_cells        : 200
gold               : 0
silver             : 0
watch              : 8
blocked            : 192
confidence_for_live: paper_only
elapsed_seconds    : 2473.2
```

## Ranked Summary Table

| model                                         | symbol    |  window | tier     |  win_rate | wilson_ci        |       rwev | wf_pos/tot |  tt_gap% | reason                                                  |
| --------------------------------------------- | --------- | ------- | -------- | --------- | ---------------- | ---------- | --------- | -------- | ------------------------------------------------------- |
| h1200_sol_v3_89d_20260427                     | SOLUSDT   |    1800 | watch    |    0.6304 | [0.4860,0.7548]  |    0.13513 |      skip |      N/A | raw_p=0.0384<0.05 but 4 gold criteria failed: n_passed= |
| h1200_btc_v3_329d_20260506                    | BTCUSDT   |    1800 | watch    |    0.6667 | [0.5254,0.7832]  |    0.12673 |       4/5 |     0.00 | raw_p=0.0105<0.05 but 2 gold criteria failed: n_passed= |
| h1800_btc_v3_89d_20260506                     | BTCUSDT   |    1800 | watch    |    0.6250 | [0.4836,0.7478]  |    0.11506 |       4/5 |     0.00 | raw_p=0.0416<0.05 but 3 gold criteria failed: n_passed= |
| h900_sol_v3_179d_20260427                     | SOLUSDT   |    1800 | watch    |    0.6304 | [0.4860,0.7548]  |    0.11035 |      skip |      N/A | raw_p=0.0384<0.05 but 4 gold criteria failed: n_passed= |
| h1800_sol_v3_329d_20260427                    | SOLUSDT   |    1800 | watch    |    0.6304 | [0.4860,0.7548]  |    0.09926 |      skip |      N/A | raw_p=0.0384<0.05 but 4 gold criteria failed: n_passed= |
| h1200_btc_v3_179d_20260506                    | BTCUSDT   |    1800 | watch    |    0.6250 | [0.4836,0.7478]  |    0.09840 |       4/5 |     0.00 | raw_p=0.0416<0.05 but 3 gold criteria failed: n_passed= |
| h300_eth_v3_329d_20260506                     | ETHUSDT   |     300 | watch    |    0.5486 | [0.4909,0.6051]  |    0.03419 |       1/5 |      N/A | raw_p=0.0495<0.05 but 3 gold criteria failed: wilson_ci |
| h60_btc_v3_329d_20260506                      | BTCUSDT   |     300 | watch    |    0.5486 | [0.4909,0.6051]  |    0.02322 |       2/5 |      N/A | raw_p=0.0495<0.05 but 3 gold criteria failed: wilson_ci |
| h600_sol_v3_89d_20260427                      | SOLUSDT   |    1800 | blocked  |    0.6087 | [0.4646,0.7361]  |    0.12122 |      skip |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h1200_sol_v3_329d_20260427                    | SOLUSDT   |    1800 | blocked  |    0.5870 | [0.4434,0.7171]  |    0.08187 |      skip |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h60_btc_v3_179d_20260506                      | BTCUSDT   |    1800 | blocked  |    0.6042 | [0.4631,0.7298]  |    0.07735 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h600_sol_v3_179d_20260506                     | SOLUSDT   |    1800 | blocked  |    0.5745 | [0.4328,0.7049]  |    0.07408 |      skip |      N/A | no significant edge or insufficient data; n_passed=47<5 |
| h1800_sol_v3_179d_20260427                    | SOLUSDT   |    1800 | blocked  |    0.5870 | [0.4434,0.7171]  |    0.07035 |      skip |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h300_sol_v3_179d_20260506                     | SOLUSDT   |     900 | blocked  |    0.5684 | [0.4681,0.6634]  |    0.06568 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1200_sol_v3_179d_20260506                    | SOLUSDT   |    1800 | blocked  |    0.5745 | [0.4328,0.7049]  |    0.05855 |      skip |      N/A | no significant edge or insufficient data; n_passed=47<5 |
| h1200_sol_v3_179d_20260427                    | SOLUSDT   |    1800 | blocked  |    0.5652 | [0.4225,0.6979]  |    0.05817 |      skip |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h900_sol_v3_89d_20260427                      | SOLUSDT   |    1800 | blocked  |    0.5652 | [0.4225,0.6979]  |    0.05752 |      skip |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h1800_btc_v3_329d_20260506                    | BTCUSDT   |    1800 | blocked  |    0.5417 | [0.4029,0.6742]  |    0.05631 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h300_btc_v3_179d_20260506                     | BTCUSDT   |     900 | blocked  |    0.5729 | [0.4730,0.6672]  |    0.05360 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h1800_sol_v3_329d_20260506                    | SOLUSDT   |    1800 | blocked  |    0.5532 | [0.4125,0.6859]  |    0.05238 |      skip |      N/A | no significant edge or insufficient data; n_passed=47<5 |
| h300_btc_v3_329d_20260506                     | BTCUSDT   |    1800 | blocked  |    0.5833 | [0.4428,0.7115]  |    0.05194 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h60_btc_v3_329d_20260506                      | BTCUSDT   |     900 | blocked  |    0.5729 | [0.4730,0.6672]  |    0.05110 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h60_sol_v3_329d_20260506                      | SOLUSDT   |    1800 | blocked  |    0.5319 | [0.3923,0.6667]  |    0.05026 |      skip |      N/A | no significant edge or insufficient data; n_passed=47<5 |
| h600_btc_v3_179d_20260506                     | BTCUSDT   |    1800 | blocked  |    0.5625 | [0.4228,0.6930]  |    0.04735 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h300_sol_v3_179d_20260506                     | SOLUSDT   |    1800 | blocked  |    0.5532 | [0.4125,0.6859]  |    0.04366 |      skip |      N/A | no significant edge or insufficient data; n_passed=47<5 |
| h1800_sol_v3_89d_20260506                     | SOLUSDT   |    1800 | blocked  |    0.5532 | [0.4125,0.6859]  |    0.04004 |      skip |      N/A | no significant edge or insufficient data; n_passed=47<5 |
| h60_sol_v3_89d_20260506                       | SOLUSDT   |     900 | blocked  |    0.5368 | [0.4371,0.6337]  |    0.03916 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h180_sol_v3_89d_20260506                      | SOLUSDT   |     900 | blocked  |    0.5579 | [0.4577,0.6536]  |    0.03800 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_sol_v3_89d_20260427                      | SOLUSDT   |    1800 | blocked  |    0.5217 | [0.3814,0.6588]  |    0.03774 |      skip |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h180_sol_v3_89d_20260506                      | SOLUSDT   |    1800 | blocked  |    0.5532 | [0.4125,0.6859]  |    0.03685 |      skip |      N/A | no significant edge or insufficient data; n_passed=47<5 |
| h900_btc_v3_329d_20260506                     | BTCUSDT   |    1800 | blocked  |    0.5625 | [0.4228,0.6930]  |    0.03485 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h1200_btc_v3_89d_20260506                     | BTCUSDT   |    1800 | blocked  |    0.5417 | [0.4029,0.6742]  |    0.03485 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h900_sol_v3_329d_20260427                     | SOLUSDT   |     900 | blocked  |    0.5326 | [0.4314,0.6312]  |    0.03176 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_sol_v3_329d_20260506                    | SOLUSDT   |     900 | blocked  |    0.5368 | [0.4371,0.6337]  |    0.03137 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1200_sol_v3_329d_20260506                    | SOLUSDT   |     900 | blocked  |    0.5368 | [0.4371,0.6337]  |    0.02832 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1200_sol_v3_89d_20260427                     | SOLUSDT   |     900 | blocked  |    0.5326 | [0.4314,0.6312]  |    0.02817 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h600_eth_v3_89d_20260506                      | ETHUSDT   |     300 | blocked  |    0.5417 | [0.4839,0.5983]  |    0.02649 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1200_sol_v3_329d_20260427                    | SOLUSDT   |     900 | blocked  |    0.5326 | [0.4314,0.6312]  |    0.02339 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_sol_v3_179d_20260506                    | SOLUSDT   |    1800 | blocked  |    0.5319 | [0.3923,0.6667]  |    0.02302 |      skip |      N/A | no significant edge or insufficient data; n_passed=47<5 |
| h1800_sol_v3_89d_20260427                     | SOLUSDT   |    1800 | blocked  |    0.5000 | [0.3612,0.6388]  |    0.02187 |      skip |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h60_sol_v3_329d_20260506                      | SOLUSDT   |     900 | blocked  |    0.5158 | [0.4167,0.6137]  |    0.02084 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_sol_v3_179d_20260506                    | SOLUSDT   |     900 | blocked  |    0.5263 | [0.4269,0.6237]  |    0.02021 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_btc_v3_329d_20260506                    | BTCUSDT   |     300 | blocked  |    0.5243 | [0.4667,0.5813]  |    0.01954 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_sol_v3_179d_20260506                      | SOLUSDT   |     900 | blocked  |    0.5158 | [0.4167,0.6137]  |    0.01947 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_btc_v3_179d_20260506                     | BTCUSDT   |    1800 | blocked  |    0.5417 | [0.4029,0.6742]  |    0.01860 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h900_btc_v3_89d_20260506                      | BTCUSDT   |    1800 | blocked  |    0.5625 | [0.4228,0.6930]  |    0.01840 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h1200_sol_v3_179d_20260506                    | SOLUSDT   |     900 | blocked  |    0.5263 | [0.4269,0.6237]  |    0.01790 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_sol_v3_89d_20260506                     | SOLUSDT   |     900 | blocked  |    0.5368 | [0.4371,0.6337]  |    0.01779 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_eth_v3_89d_20260506                      | ETHUSDT   |     300 | blocked  |    0.5417 | [0.4839,0.5983]  |    0.01739 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h600_btc_v3_89d_20260506                      | BTCUSDT   |     300 | blocked  |    0.5312 | [0.4736,0.5881]  |    0.01715 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h180_btc_v3_89d_20260506                      | BTCUSDT   |     300 | blocked  |    0.5208 | [0.4632,0.5779]  |    0.01673 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_btc_v3_89d_20260506                       | BTCUSDT   |    1800 | blocked  |    0.5208 | [0.3833,0.6553]  |    0.01485 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h60_sol_v3_179d_20260506                      | SOLUSDT   |    1800 | blocked  |    0.5106 | [0.3724,0.6472]  |    0.01451 |      skip |      N/A | no significant edge or insufficient data; n_passed=47<5 |
| h1200_btc_v3_329d_20260506                    | BTCUSDT   |     300 | blocked  |    0.5243 | [0.4667,0.5813]  |    0.01388 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_eth_v3_179d_20260506                     | ETHUSDT   |     300 | blocked  |    0.5278 | [0.4701,0.5847]  |    0.01326 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_btc_v3_89d_20260506                      | BTCUSDT   |     900 | blocked  |    0.5521 | [0.4525,0.6476]  |    0.01267 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h1800_sol_v3_179d_20260427                    | SOLUSDT   |     900 | blocked  |    0.5217 | [0.4209,0.6209]  |    0.01263 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h180_btc_v3_329d_20260506                     | BTCUSDT   |     300 | blocked  |    0.5278 | [0.4701,0.5847]  |    0.01107 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_sol_v3_89d_20260506                      | SOLUSDT   |     900 | blocked  |    0.5263 | [0.4269,0.6237]  |    0.00905 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_btc_v3_179d_20260506                    | BTCUSDT   |    1800 | blocked  |    0.5000 | [0.3639,0.6361]  |    0.00881 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h1200_sol_v3_89d_20260506                     | SOLUSDT   |     900 | blocked  |    0.5158 | [0.4167,0.6137]  |    0.00832 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1200_btc_v3_179d_20260506                    | BTCUSDT   |     300 | blocked  |    0.5104 | [0.4529,0.5676]  |    0.00715 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h180_sol_v3_89d_20260427                      | SOLUSDT   |    1800 | blocked  |    0.4783 | [0.3412,0.6186]  |    0.00709 |      skip |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h1800_sol_v3_179d_20260506                    | SOLUSDT   |     300 | blocked  |    0.5157 | [0.4580,0.5729]  |    0.00609 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h300_btc_v3_179d_20260506                     | BTCUSDT   |     300 | blocked  |    0.5208 | [0.4632,0.5779]  |    0.00544 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_sol_v3_89d_20260427                       | SOLUSDT   |     900 | blocked  |    0.4891 | [0.3895,0.5896]  |    0.00513 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_sol_v3_89d_20260506                      | SOLUSDT   |     900 | blocked  |    0.5158 | [0.4167,0.6137]  |    0.00463 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_btc_v3_179d_20260506                     | BTCUSDT   |     300 | blocked  |    0.5139 | [0.4564,0.5710]  |    0.00454 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_sol_v3_329d_20260427                     | SOLUSDT   |     300 | blocked  |    0.5126 | [0.4540,0.5709]  |    0.00412 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h300_sol_v3_329d_20260427                     | SOLUSDT   |     900 | blocked  |    0.4891 | [0.3895,0.5896]  |    0.00372 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h600_sol_v3_179d_20260506                     | SOLUSDT   |     300 | blocked  |    0.5226 | [0.4649,0.5798]  |    0.00239 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h1200_sol_v3_329d_20260506                    | SOLUSDT   |     300 | blocked  |    0.5157 | [0.4580,0.5729]  |    0.00208 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h1800_sol_v3_329d_20260427                    | SOLUSDT   |     300 | blocked  |    0.5199 | [0.4612,0.5780]  |    0.00113 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h300_btc_v3_329d_20260506                     | BTCUSDT   |     300 | blocked  |    0.5243 | [0.4667,0.5813]  |    0.00055 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_sol_v3_89d_20260506                       | SOLUSDT   |     300 | blocked  |    0.5052 | [0.4477,0.5626]  |   -0.00074 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h180_sol_v3_179d_20260506                     | SOLUSDT   |     900 | blocked  |    0.5053 | [0.4065,0.6036]  |   -0.00221 |       3/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1200_btc_v3_179d_20260506                    | BTCUSDT   |     900 | blocked  |    0.5000 | [0.4019,0.5981]  |   -0.00223 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h600_btc_v3_89d_20260506                      | BTCUSDT   |    1800 | blocked  |    0.5417 | [0.4029,0.6742]  |   -0.00348 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h600_eth_v3_329d_20260506                     | ETHUSDT   |     300 | blocked  |    0.5139 | [0.4564,0.5710]  |   -0.00396 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_btc_v3_179d_20260506                     | BTCUSDT   |    1800 | blocked  |    0.5208 | [0.3833,0.6553]  |   -0.00556 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h300_btc_v3_89d_20260506                      | BTCUSDT   |     300 | blocked  |    0.5174 | [0.4598,0.5745]  |   -0.00608 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_btc_v3_89d_20260506                      | BTCUSDT   |     300 | blocked  |    0.5104 | [0.4529,0.5676]  |   -0.00608 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_btc_v3_89d_20260506                      | BTCUSDT   |    1800 | blocked  |    0.5417 | [0.4029,0.6742]  |   -0.00681 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h1800_sol_v3_329d_20260506                    | SOLUSDT   |     300 | blocked  |    0.5052 | [0.4477,0.5626]  |   -0.00740 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h1800_sol_v3_89d_20260427                     | SOLUSDT   |     300 | blocked  |    0.5018 | [0.4433,0.5603]  |   -0.00750 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h180_btc_v3_89d_20260506                      | BTCUSDT   |    1800 | blocked  |    0.5000 | [0.3639,0.6361]  |   -0.00785 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h600_btc_v3_179d_20260506                     | BTCUSDT   |     300 | blocked  |    0.5000 | [0.4426,0.5574]  |   -0.00803 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_btc_v3_179d_20260506                      | BTCUSDT   |     900 | blocked  |    0.5208 | [0.4220,0.6180]  |   -0.00890 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h900_sol_v3_89d_20260427                      | SOLUSDT   |     300 | blocked  |    0.4982 | [0.4397,0.5567]  |   -0.01122 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h1200_btc_v3_329d_20260506                    | BTCUSDT   |     900 | blocked  |    0.5104 | [0.4120,0.6081]  |   -0.01285 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h600_eth_v3_179d_20260506                     | ETHUSDT   |     300 | blocked  |    0.5069 | [0.4495,0.5642]  |   -0.01285 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_btc_v3_179d_20260506                    | BTCUSDT   |     300 | blocked  |    0.4826 | [0.4255,0.5402]  |   -0.01310 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_sol_v3_179d_20260506                      | SOLUSDT   |     300 | blocked  |    0.4948 | [0.4374,0.5523]  |   -0.01433 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h180_btc_v3_179d_20260506                     | BTCUSDT   |     300 | blocked  |    0.5000 | [0.4426,0.5574]  |   -0.01442 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_eth_v3_329d_20260427                    | ETHUSDT   |     300 | blocked  |    0.4946 | [0.4362,0.5531]  |   -0.01458 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_eth_v3_179d_20260506                     | ETHUSDT   |     300 | blocked  |    0.5069 | [0.4495,0.5642]  |   -0.01511 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_btc_v3_89d_20260506                     | BTCUSDT   |     900 | blocked  |    0.4792 | [0.3820,0.5780]  |   -0.01567 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h900_sol_v3_329d_20260506                     | SOLUSDT   |     300 | blocked  |    0.5017 | [0.4443,0.5592]  |   -0.01597 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h600_sol_v3_179d_20260427                     | SOLUSDT   |     300 | blocked  |    0.5018 | [0.4433,0.5603]  |   -0.01609 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h600_sol_v3_329d_20260427                     | SOLUSDT   |     300 | blocked  |    0.5090 | [0.4504,0.5674]  |   -0.01642 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h1800_sol_v3_179d_20260427                    | SOLUSDT   |     300 | blocked  |    0.4946 | [0.4362,0.5531]  |   -0.01754 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h1200_eth_v3_179d_20260427                    | ETHUSDT   |     300 | blocked  |    0.4838 | [0.4255,0.5424]  |   -0.01844 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_sol_v3_179d_20260506                     | SOLUSDT   |     300 | blocked  |    0.4948 | [0.4374,0.5523]  |   -0.01921 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h1200_eth_v3_329d_20260506                    | ETHUSDT   |     300 | blocked  |    0.4792 | [0.4221,0.5368]  |   -0.02091 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_eth_v3_89d_20260506                       | ETHUSDT   |     300 | blocked  |    0.4931 | [0.4358,0.5505]  |   -0.02108 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_sol_v3_179d_20260427                     | SOLUSDT   |     300 | blocked  |    0.4982 | [0.4397,0.5567]  |   -0.02118 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h600_sol_v3_89d_20260427                      | SOLUSDT   |     300 | blocked  |    0.4910 | [0.4326,0.5496]  |   -0.02133 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h900_btc_v3_329d_20260506                     | BTCUSDT   |     300 | blocked  |    0.4826 | [0.4255,0.5402]  |   -0.02167 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_eth_v3_89d_20260427                     | ETHUSDT   |     300 | blocked  |    0.4801 | [0.4220,0.5388]  |   -0.02205 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_eth_v3_179d_20260427                    | ETHUSDT   |     300 | blocked  |    0.4838 | [0.4255,0.5424]  |   -0.02219 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_eth_v3_179d_20260506                    | ETHUSDT   |     300 | blocked  |    0.4896 | [0.4324,0.5471]  |   -0.02327 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_btc_v3_89d_20260506                     | BTCUSDT   |     300 | blocked  |    0.4792 | [0.4221,0.5368]  |   -0.02463 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_btc_v3_329d_20260506                     | BTCUSDT   |     900 | blocked  |    0.5104 | [0.4120,0.6081]  |   -0.02463 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h1200_btc_v3_89d_20260506                     | BTCUSDT   |     300 | blocked  |    0.4722 | [0.4153,0.5299]  |   -0.02490 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h180_btc_v3_179d_20260506                     | BTCUSDT   |     900 | blocked  |    0.5000 | [0.4019,0.5981]  |   -0.02577 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h60_eth_v3_179d_20260506                      | ETHUSDT   |     300 | blocked  |    0.4931 | [0.4358,0.5505]  |   -0.02612 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h180_sol_v3_179d_20260506                     | SOLUSDT   |     300 | blocked  |    0.4843 | [0.4271,0.5420]  |   -0.02621 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h1200_sol_v3_89d_20260427                     | SOLUSDT   |     300 | blocked  |    0.4874 | [0.4291,0.5460]  |   -0.02631 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h60_sol_v3_329d_20260506                      | SOLUSDT   |     300 | blocked  |    0.4808 | [0.4237,0.5385]  |   -0.02701 |       0/5 |     2.16 | no significant edge or insufficient data; wilson_ci_low |
| h1200_eth_v3_329d_20260427                    | ETHUSDT   |     300 | blocked  |    0.4693 | [0.4114,0.5281]  |   -0.03035 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_eth_v3_89d_20260506                      | ETHUSDT   |     300 | blocked  |    0.4826 | [0.4255,0.5402]  |   -0.03105 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_eth_v3_329d_20260506                     | ETHUSDT   |     300 | blocked  |    0.4722 | [0.4153,0.5299]  |   -0.03240 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_eth_v3_329d_20260506                      | ETHUSDT   |     300 | blocked  |    0.4757 | [0.4187,0.5333]  |   -0.03365 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_btc_v3_89d_20260506                       | BTCUSDT   |     900 | blocked  |    0.4688 | [0.3721,0.5678]  |   -0.03567 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h1200_eth_v3_179d_20260506                    | ETHUSDT   |     300 | blocked  |    0.4722 | [0.4153,0.5299]  |   -0.03640 |       1/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_btc_v3_89d_20260506                       | BTCUSDT   |     300 | blocked  |    0.4757 | [0.4187,0.5333]  |   -0.03858 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h600_btc_v3_329d_20260506                     | BTCUSDT   |    1800 | blocked  |    0.5000 | [0.3639,0.6361]  |   -0.04077 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h60_btc_v3_179d_20260506                      | BTCUSDT   |     300 | blocked  |    0.4757 | [0.4187,0.5333]  |   -0.04133 |       2/5 |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1200_btc_v3_89d_20260506                     | BTCUSDT   |     900 | blocked  |    0.4479 | [0.3524,0.5475]  |   -0.04296 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h180_btc_v3_329d_20260506                     | BTCUSDT   |    1800 | blocked  |    0.4792 | [0.3447,0.6167]  |   -0.05265 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h1800_btc_v3_179d_20260506                    | BTCUSDT   |     900 | blocked  |    0.4271 | [0.3328,0.5270]  |   -0.05390 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h900_btc_v3_329d_20260506                     | BTCUSDT   |     900 | blocked  |    0.4479 | [0.3524,0.5475]  |   -0.06348 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h180_btc_v3_89d_20260506                      | BTCUSDT   |     900 | blocked  |    0.4375 | [0.3426,0.5372]  |   -0.06942 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h60_btc_v3_329d_20260506                      | BTCUSDT   |    1800 | blocked  |    0.4583 | [0.3258,0.5971]  |   -0.06994 |       4/5 |     0.00 | no significant edge or insufficient data; n_passed=48<5 |
| h900_btc_v3_179d_20260506                     | BTCUSDT   |     900 | blocked  |    0.4271 | [0.3328,0.5270]  |   -0.08712 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h600_btc_v3_89d_20260506                      | BTCUSDT   |     900 | blocked  |    0.4271 | [0.3328,0.5270]  |   -0.08942 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h600_btc_v3_179d_20260506                     | BTCUSDT   |     900 | blocked  |    0.4167 | [0.3231,0.5166]  |   -0.09546 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h1800_btc_v3_329d_20260506                    | BTCUSDT   |     900 | blocked  |    0.3958 | [0.3038,0.4958]  |   -0.09681 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h600_btc_v3_329d_20260506                     | BTCUSDT   |     900 | blocked  |    0.4271 | [0.3328,0.5270]  |   -0.09713 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h900_btc_v3_89d_20260506                      | BTCUSDT   |     900 | blocked  |    0.4271 | [0.3328,0.5270]  |   -0.09910 |       0/5 |     0.42 | no significant edge or insufficient data; wilson_ci_low |
| h60_xrp_v3_179d_20260427                      | XRPUSDT   |     300 | blocked  |    0.4693 | [0.4114,0.5281]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_xrp_v3_329d_20260427                      | XRPUSDT   |     300 | blocked  |    0.4874 | [0.4291,0.5460]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_xrp_v3_89d_20260427                       | XRPUSDT   |     300 | blocked  |    0.4657 | [0.4078,0.5245]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h180_xrp_v3_179d_20260427                     | XRPUSDT   |     300 | blocked  |    0.4657 | [0.4078,0.5245]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h180_xrp_v3_329d_20260427                     | XRPUSDT   |     300 | blocked  |    0.4477 | [0.3902,0.5065]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h180_xrp_v3_89d_20260427                      | XRPUSDT   |     300 | blocked  |    0.4765 | [0.4184,0.5353]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_xrp_v3_179d_20260427                     | XRPUSDT   |     300 | blocked  |    0.4404 | [0.3832,0.4993]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_xrp_v3_329d_20260427                     | XRPUSDT   |     300 | blocked  |    0.4477 | [0.3902,0.5065]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h300_xrp_v3_89d_20260427                      | XRPUSDT   |     300 | blocked  |    0.4657 | [0.4078,0.5245]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h600_xrp_v3_179d_20260427                     | XRPUSDT   |     300 | blocked  |    0.5271 | [0.4683,0.5851]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h600_xrp_v3_329d_20260427                     | XRPUSDT   |     300 | blocked  |    0.5090 | [0.4504,0.5674]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h600_xrp_v3_89d_20260427                      | XRPUSDT   |     300 | blocked  |    0.4838 | [0.4255,0.5424]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_xrp_v3_179d_20260427                     | XRPUSDT   |     300 | blocked  |    0.5018 | [0.4433,0.5603]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_xrp_v3_329d_20260427                     | XRPUSDT   |     300 | blocked  |    0.4765 | [0.4184,0.5353]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h900_xrp_v3_89d_20260427                      | XRPUSDT   |     300 | blocked  |    0.4982 | [0.4397,0.5567]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1200_xrp_v3_179d_20260427                    | XRPUSDT   |     300 | blocked  |    0.5487 | [0.4899,0.6063]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1200_xrp_v3_329d_20260427                    | XRPUSDT   |     300 | blocked  |    0.5199 | [0.4612,0.5780]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1200_xrp_v3_89d_20260427                     | XRPUSDT   |     300 | blocked  |    0.4982 | [0.4397,0.5567]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_xrp_v3_179d_20260427                    | XRPUSDT   |     300 | blocked  |    0.5090 | [0.4504,0.5674]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h1800_xrp_v3_329d_20260427                    | XRPUSDT   |     300 | blocked  |    0.4765 | [0.4184,0.5353]  |    0.00000 |      skip |      N/A | no significant edge or insufficient data; wilson_ci_low |
| h60_xrp_v3_179d_20260427                      | XRPUSDT   |     900 | blocked  |    0.4348 | [0.3381,0.5367]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h60_xrp_v3_329d_20260427                      | XRPUSDT   |     900 | blocked  |    0.4239 | [0.3280,0.5259]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h60_xrp_v3_89d_20260427                       | XRPUSDT   |     900 | blocked  |    0.4457 | [0.3483,0.5474]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h180_xrp_v3_179d_20260427                     | XRPUSDT   |     900 | blocked  |    0.5109 | [0.4104,0.6105]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h180_xrp_v3_329d_20260427                     | XRPUSDT   |     900 | blocked  |    0.4565 | [0.3585,0.5580]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h180_xrp_v3_89d_20260427                      | XRPUSDT   |     900 | blocked  |    0.4457 | [0.3483,0.5474]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h300_xrp_v3_179d_20260427                     | XRPUSDT   |     900 | blocked  |    0.4565 | [0.3585,0.5580]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h300_xrp_v3_329d_20260427                     | XRPUSDT   |     900 | blocked  |    0.4783 | [0.3791,0.5791]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h300_xrp_v3_89d_20260427                      | XRPUSDT   |     900 | blocked  |    0.5000 | [0.3999,0.6001]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h600_xrp_v3_179d_20260427                     | XRPUSDT   |     900 | blocked  |    0.4457 | [0.3483,0.5474]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h600_xrp_v3_329d_20260427                     | XRPUSDT   |     900 | blocked  |    0.4674 | [0.3688,0.5686]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h600_xrp_v3_89d_20260427                      | XRPUSDT   |     900 | blocked  |    0.4783 | [0.3791,0.5791]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h900_xrp_v3_179d_20260427                     | XRPUSDT   |     900 | blocked  |    0.3804 | [0.2879,0.4825]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h900_xrp_v3_329d_20260427                     | XRPUSDT   |     900 | blocked  |    0.4783 | [0.3791,0.5791]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h900_xrp_v3_89d_20260427                      | XRPUSDT   |     900 | blocked  |    0.4565 | [0.3585,0.5580]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h1200_xrp_v3_179d_20260427                    | XRPUSDT   |     900 | blocked  |    0.4783 | [0.3791,0.5791]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h1200_xrp_v3_329d_20260427                    | XRPUSDT   |     900 | blocked  |    0.4239 | [0.3280,0.5259]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h1200_xrp_v3_89d_20260427                     | XRPUSDT   |     900 | blocked  |    0.4348 | [0.3381,0.5367]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h1800_xrp_v3_179d_20260427                    | XRPUSDT   |     900 | blocked  |    0.3804 | [0.2879,0.4825]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h1800_xrp_v3_329d_20260427                    | XRPUSDT   |     900 | blocked  |    0.4891 | [0.3895,0.5896]  |    0.00000 |       0/5 |    -6.92 | no significant edge or insufficient data; wilson_ci_low |
| h60_xrp_v3_179d_20260427                      | XRPUSDT   |    1800 | blocked  |    0.4565 | [0.3215,0.5982]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h60_xrp_v3_329d_20260427                      | XRPUSDT   |    1800 | blocked  |    0.4348 | [0.3021,0.5775]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h60_xrp_v3_89d_20260427                       | XRPUSDT   |    1800 | blocked  |    0.3913 | [0.2639,0.5354]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h180_xrp_v3_179d_20260427                     | XRPUSDT   |    1800 | blocked  |    0.5870 | [0.4434,0.7171]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h180_xrp_v3_329d_20260427                     | XRPUSDT   |    1800 | blocked  |    0.5652 | [0.4225,0.6979]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h180_xrp_v3_89d_20260427                      | XRPUSDT   |    1800 | blocked  |    0.4565 | [0.3215,0.5982]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h300_xrp_v3_179d_20260427                     | XRPUSDT   |    1800 | blocked  |    0.4565 | [0.3215,0.5982]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h300_xrp_v3_329d_20260427                     | XRPUSDT   |    1800 | blocked  |    0.4348 | [0.3021,0.5775]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h300_xrp_v3_89d_20260427                      | XRPUSDT   |    1800 | blocked  |    0.3913 | [0.2639,0.5354]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h600_xrp_v3_179d_20260427                     | XRPUSDT   |    1800 | blocked  |    0.2826 | [0.1732,0.4255]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h600_xrp_v3_329d_20260427                     | XRPUSDT   |    1800 | blocked  |    0.3696 | [0.2452,0.5140]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h600_xrp_v3_89d_20260427                      | XRPUSDT   |    1800 | blocked  |    0.3696 | [0.2452,0.5140]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h900_xrp_v3_179d_20260427                     | XRPUSDT   |    1800 | blocked  |    0.5435 | [0.4018,0.6785]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h900_xrp_v3_329d_20260427                     | XRPUSDT   |    1800 | blocked  |    0.3913 | [0.2639,0.5354]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h900_xrp_v3_89d_20260427                      | XRPUSDT   |    1800 | blocked  |    0.4565 | [0.3215,0.5982]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h1200_xrp_v3_179d_20260427                    | XRPUSDT   |    1800 | blocked  |    0.4348 | [0.3021,0.5775]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h1200_xrp_v3_329d_20260427                    | XRPUSDT   |    1800 | blocked  |    0.3913 | [0.2639,0.5354]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h1200_xrp_v3_89d_20260427                     | XRPUSDT   |    1800 | blocked  |    0.3261 | [0.2087,0.4703]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h1800_xrp_v3_179d_20260427                    | XRPUSDT   |    1800 | blocked  |    0.3696 | [0.2452,0.5140]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |
| h1800_xrp_v3_329d_20260427                    | XRPUSDT   |    1800 | blocked  |    0.5000 | [0.3612,0.6388]  |    0.00000 |       0/5 |      N/A | no significant edge or insufficient data; n_passed=46<5 |


## Per-Cell Detail (8 qualifying cells)

### WATCH — h1200_sol_v3_89d_20260427 | SOLUSDT / 1800s

- **Tier:** watch  |  **Kelly multiplier:** 0.0
- **Reason:** raw_p=0.0384<0.05 but 4 gold criteria failed: n_passed=46<50; wilson_ci_lower=0.4860<=0.515; walk_forward_skipped:insufficient_data
- **n_passed:** 46  |  **win_rate:** 0.630435  |  **wilson_ci:** [0.486001, 0.754762]
- **ev_per_trade (rwev proxy):** 0.13513  |  **roi_pct:** 13.513  |  **sharpe:** 1.9784
- **raw_p:** 0.038422  |  **cross_cell_adj_p:** 0.920036
- **best_confidence_threshold:** 0.53
- **decay_slope_negative:** False
- **walk_forward_skipped:** error: timed out
- **train_test:** train_wr=None  test_wr=None  gap=Nonepp
- **regime_favorites:** ?=0.63

**Recommended filter_config (copy-paste):**
```json
{
  "confidence_threshold": 0.58,
  "consensus_required": true,
  "blackout_hours": [
    0,
    1,
    2,
    3,
    4,
    5
  ]
}
```


### WATCH — h1200_btc_v3_329d_20260506 | BTCUSDT / 1800s

- **Tier:** watch  |  **Kelly multiplier:** 0.0
- **Reason:** raw_p=0.0105<0.05 but 2 gold criteria failed: n_passed=48<50; cross_cell_adj_p=0.920036 not<0.05
- **n_passed:** 48  |  **win_rate:** 0.666667  |  **wilson_ci:** [0.525401, 0.783232]
- **ev_per_trade (rwev proxy):** 0.126729  |  **roi_pct:** 12.6729  |  **sharpe:** 1.8291
- **raw_p:** 0.010461  |  **cross_cell_adj_p:** 0.920036
- **best_confidence_threshold:** 0.51
- **decay_slope_negative:** False
- **walk_forward:** 4/5 EV-positive folds  |  mean_roi: 51.2944  |  robust: True
- **train_test:** train_wr=1.0  test_wr=1.0  gap=0.0pp
- **regime_favorites:** ?=0.67

**Recommended filter_config (copy-paste):**
```json
{
  "confidence_threshold": 0.56,
  "consensus_required": true,
  "blackout_hours": [
    0,
    1,
    2,
    3,
    4,
    5
  ]
}
```


### WATCH — h1800_btc_v3_89d_20260506 | BTCUSDT / 1800s

- **Tier:** watch  |  **Kelly multiplier:** 0.0
- **Reason:** raw_p=0.0416<0.05 but 3 gold criteria failed: n_passed=48<50; wilson_ci_lower=0.4836<=0.515; cross_cell_adj_p=0.920036 not<0.05
- **n_passed:** 48  |  **win_rate:** 0.625  |  **wilson_ci:** [0.483628, 0.747847]
- **ev_per_trade (rwev proxy):** 0.115062  |  **roi_pct:** 11.5062  |  **sharpe:** 1.6497
- **raw_p:** 0.041632  |  **cross_cell_adj_p:** 0.920036
- **best_confidence_threshold:** 0.54
- **decay_slope_negative:** False
- **walk_forward:** 4/5 EV-positive folds  |  mean_roi: 51.2944  |  robust: True
- **train_test:** train_wr=1.0  test_wr=1.0  gap=0.0pp
- **regime_favorites:** ?=0.62

**Recommended filter_config (copy-paste):**
```json
{
  "confidence_threshold": 0.56,
  "consensus_required": true,
  "blackout_hours": [
    0,
    1,
    2,
    3,
    4,
    5
  ]
}
```


### WATCH — h900_sol_v3_179d_20260427 | SOLUSDT / 1800s

- **Tier:** watch  |  **Kelly multiplier:** 0.0
- **Reason:** raw_p=0.0384<0.05 but 4 gold criteria failed: n_passed=46<50; wilson_ci_lower=0.4860<=0.515; walk_forward_skipped:insufficient_data
- **n_passed:** 46  |  **win_rate:** 0.630435  |  **wilson_ci:** [0.486001, 0.754762]
- **ev_per_trade (rwev proxy):** 0.110348  |  **roi_pct:** 11.0348  |  **sharpe:** 1.591
- **raw_p:** 0.038422  |  **cross_cell_adj_p:** 0.920036
- **best_confidence_threshold:** 0.52
- **decay_slope_negative:** False
- **walk_forward_skipped:** error: timed out
- **train_test:** train_wr=None  test_wr=None  gap=Nonepp
- **regime_favorites:** ?=0.63

**Recommended filter_config (copy-paste):**
```json
{
  "confidence_threshold": 0.58,
  "consensus_required": true,
  "blackout_hours": [
    0,
    1,
    2,
    3,
    4,
    5
  ]
}
```


### WATCH — h1800_sol_v3_329d_20260427 | SOLUSDT / 1800s

- **Tier:** watch  |  **Kelly multiplier:** 0.0
- **Reason:** raw_p=0.0384<0.05 but 4 gold criteria failed: n_passed=46<50; wilson_ci_lower=0.4860<=0.515; walk_forward_skipped:insufficient_data
- **n_passed:** 46  |  **win_rate:** 0.630435  |  **wilson_ci:** [0.486001, 0.754762]
- **ev_per_trade (rwev proxy):** 0.099261  |  **roi_pct:** 9.9261  |  **sharpe:** 1.4166
- **raw_p:** 0.038422  |  **cross_cell_adj_p:** 0.920036
- **best_confidence_threshold:** 0.5
- **decay_slope_negative:** False
- **walk_forward_skipped:** error: timed out
- **train_test:** train_wr=None  test_wr=None  gap=Nonepp
- **regime_favorites:** ?=0.63

**Recommended filter_config (copy-paste):**
```json
{
  "confidence_threshold": 0.58,
  "consensus_required": true,
  "blackout_hours": [
    0,
    1,
    2,
    3,
    4,
    5
  ]
}
```


### WATCH — h1200_btc_v3_179d_20260506 | BTCUSDT / 1800s

- **Tier:** watch  |  **Kelly multiplier:** 0.0
- **Reason:** raw_p=0.0416<0.05 but 3 gold criteria failed: n_passed=48<50; wilson_ci_lower=0.4836<=0.515; cross_cell_adj_p=0.920036 not<0.05
- **n_passed:** 48  |  **win_rate:** 0.625  |  **wilson_ci:** [0.483628, 0.747847]
- **ev_per_trade (rwev proxy):** 0.098396  |  **roi_pct:** 9.8396  |  **sharpe:** 1.399
- **raw_p:** 0.041632  |  **cross_cell_adj_p:** 0.920036
- **best_confidence_threshold:** 0.51
- **decay_slope_negative:** False
- **walk_forward:** 4/5 EV-positive folds  |  mean_roi: 51.2944  |  robust: True
- **train_test:** train_wr=1.0  test_wr=1.0  gap=0.0pp
- **regime_favorites:** ?=0.62

**Recommended filter_config (copy-paste):**
```json
{
  "confidence_threshold": 0.56,
  "consensus_required": true,
  "blackout_hours": [
    0,
    1,
    2,
    3,
    4,
    5
  ]
}
```


### WATCH — h300_eth_v3_329d_20260506 | ETHUSDT / 300s

- **Tier:** watch  |  **Kelly multiplier:** 0.0
- **Reason:** raw_p=0.0495<0.05 but 3 gold criteria failed: wilson_ci_lower=0.4909<=0.515; wf_ev_pos_folds=1/5<2/3; cross_cell_adj_p=0.920036 not<0.05
- **n_passed:** 288  |  **win_rate:** 0.548611  |  **wilson_ci:** [0.490875, 0.605068]
- **ev_per_trade (rwev proxy):** 0.034194  |  **roi_pct:** 3.4194  |  **sharpe:** 1.1561
- **raw_p:** 0.04948  |  **cross_cell_adj_p:** 0.920036
- **best_confidence_threshold:** None
- **decay_slope_negative:** None
- **walk_forward:** 1/5 EV-positive folds  |  mean_roi: -2.1138  |  robust: False
- **train_test:** train_wr=None  test_wr=None  gap=Nonepp

**Recommended filter_config (copy-paste):**
```json
{}
```

**Errors in this cell:**
- threshold_grid: timed out
- regime_matrix: timed out
- grid_search: timed out
- decay_filter: timed out
- committee_sim: timed out

### WATCH — h60_btc_v3_329d_20260506 | BTCUSDT / 300s

- **Tier:** watch  |  **Kelly multiplier:** 0.0
- **Reason:** raw_p=0.0495<0.05 but 3 gold criteria failed: wilson_ci_lower=0.4909<=0.515; wf_ev_pos_folds=2/5<2/3; cross_cell_adj_p=0.920036 not<0.05
- **n_passed:** 288  |  **win_rate:** 0.548611  |  **wilson_ci:** [0.490875, 0.605068]
- **ev_per_trade (rwev proxy):** 0.023222  |  **roi_pct:** 2.3222  |  **sharpe:** 0.8083
- **raw_p:** 0.04948  |  **cross_cell_adj_p:** 0.920036
- **best_confidence_threshold:** 0.54
- **decay_slope_negative:** None
- **walk_forward:** 2/5 EV-positive folds  |  mean_roi: -0.6218  |  robust: False
- **train_test:** train_wr=None  test_wr=None  gap=Nonepp

**Recommended filter_config (copy-paste):**
```json
{}
```

**Errors in this cell:**
- decay_filter: timed out
