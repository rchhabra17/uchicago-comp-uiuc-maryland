## 2026

## UCHICAGO

## TRADING

## COMPETITION

#### Case Packet & Information

```
Career
Advancement
Financial
Markets
```

# THANK YOU TO OUR SPONSORS!


#### WELCOME

On behalf of the University of Chicago
and the UChicago Financial Markets
Program (FM), we are pleased to

welcome you to the 14th Annual
UChicago Trading Competition! We are
excited to have you as part of our
competition this year. Thank you to our
Financial Markets Program student case
writers and platform developers, and our
corporate sponsors for their leadership
and support.

This competition could not be possible
without the generous support of our
corporate sponsors. This year’s sponsors
include: DRW, DE Shaw, Belvedere
Trading, Chicago Trading Company,
Citadel, Flow Traders, Hudson River
Trading, IMC, Jane Street, Old Mission
Capital, Optiver, SIG, TransMarket Group,
Millenium Partners, Trexquant, All
Options, and Aquatic.

```
We are delighted to have DRW as a
Platinum Sponsor this year!
```
```
The UChicago Trading Competition will
be held in-person on Saturday, April 11th
at Convene Willis Tower in downtown
Chicago. The focus of this event will be
algorithmic trading, with two cases
covering the following themes:
```
1.  Market Making
2.  Portfolio Optimization

```
We are excited to have so many talented
students in this year’s competition, and
we look forward to seeing the teams in
action!
```
```
For more information about UChicago
Career Advancement, visit our website.
```
###### TABLE OF CONTENTS

```
SCHEDULE  OF EVENTS 1
```
```
PARTICIPANTS 2
AWARDS 2
```
```
COMPETITION TECHNOLOGY 3
```
```
CASE 1: Market Making 4
```
```
CASE 2: Portfolio Optimization 12
```
### SCHEDULE  OF EVENTS

```
Friday, April 10, 2026 (all times in CDT)
```
```
Location Time (CDT) Topic
```
```
Kimpton
Gray Hotel
(122 W.
Monroe)
```
```
5:00 - 8:00 pm Poker Tournament
Sponsored by DRW
```
```
Saturday, April 11, 2026 (all times in CDT)
```
```
Location Time Topic
```
```
Convene -
Willis Tower
(233 S.
Wacker Drive)
```
```
8:00 – 8:45 am Breakfast and Participant Arrival
```
```
8:45 – 9:00 am Welcome and Agenda Overview
```
```
9:00 – 9:15 am Tech Case Prep
```
```
9:15 – 12:15 pm Case 1 Live Trading
```
```
12:15 – 2:00 pm Lunch and Employer Career Fair
```
```
2:00 – 2:45 pm Overview of Cases 1 & 2 and Q&A
```
```
2:45 – 4:15 pm Networking Reception
```
```
4:15 – 5:00 pm Awards Presentation
```
```
All sessions are required for participants to be eligible for prize money
2026 UChicago Trading Competition | 1
```

#### PARTICIPANTS

We are pleased to announce that over 150 students across the United States will
participate in this year’s competition. The following institutions will be represented:

```
Brown University
California Institute of Technology (Caltech)
Carnegie Mellon University
Columbia University
Cornell University
Drexel University
Emory University
Georgia Institute of Technology
Harvard University
Johns Hopkins University
Massachusetts Institute of Technology
New Jersey Institute of Technology
Northwestern University
Princeton University
Purdue University
Rice University
```
```
Stanford University
University of Chicago
University of Florida
University of Illinois Urbana-Champaign
University of Maryland
University of Minnesota
University of Pennsylvania
University of Southern California
University of Texas at Austin
University of Washington
University of California, Berkeley
University of California, Los Angeles
Vanderbilt University
Williams College
Yale University
```
#### AWARDS

Awards will be announced during the awards ceremony. Cash prizes will be
awarded to the winning team of each individual case and the top three overall
winners based on aggregate scores across all cases. **Participants must attend all
sessions on Friday and Saturday to be eligible for prizes.**

#### COMPETITION TECHNOLOGY

```
The University of Chicago Financial Markets Program (FM) is excited to show its in-
house trading platform Χ-Change built by members of the FM program for the 14th
Annual UChicago Trading Competition!  Case 1 will be run live utilizing this platform.
 Case 2 will be run before the competition and results will be played back at the
event.
```
##### |Algorithm Development

```
Competitors may develop their
algorithms in any computing language;
however, Python will be the only
officially supported language. No other
languages will receive explicit support
from the case writing team. On the day
of the competition, one user from each
team will be responsible for manually
starting the team's algo at the beginning
of each case round.
```
```
Additional details on rules and
requirements for each round can be
found in the case descriptions.
```
##### |Case Submission Dates

```
CASE 1
Competitors will run their finalized
algorithms locally on the day of the
competition.
```
```
CASE 2
Final code submitted by 11:59pm CST
on Thursday, April 9.
```
```
This case will be run in advance of the
competition. Teams will not run their
algorithms live on the day of the
competition. Final scores will be
announced on the day of the
competition.
```

### CASE 1:

### Market Making

##### |Introduction

```
In this case, you have been tasked to trade two stocks, one ETF, two
prediction markets, and option contracts on a third stock (put/call).
Your goal is to make as much money as you can, trading against other
competitors and the bots that already exist on the exchange.
```
```
This case involves the stocks A (a small-cap stock) and C (a large-cap
stock). We also provide call and put options contracts on stock B as
well as the ETF , which consists of 1 share of each of the 3 stocks.
Beyond the stocks, participants can trade on one prediction market
regarding hypothetical Federal Reserve rates and one “meta” market
which will be provided the day of the competition.
```
```
The case is structured as a series of successive rounds. Each round
consists of 10 days, each day is 90 seconds long, and each second
holds 5 ticks. Your positions hold over from day-to-day, and reset at
the end of each round.
```
```
A is a traditional small-cap stock which releases earnings. You will
receive their quarterly earnings as a structured news message twice
per day, and based on a constant P/E ratio and the released earnings,
you should calculate the new price of the stock.
```
```
B is a liquid semiconductor company, but you will not have any
information regarding the stock itself, so it’s advised to focus on the
options contracts. There is one underlying path for B over each round,
and at each tick, you will be provided quotes for a single-expiry
European option chain across 3 strikes around spot.
```
```
C is more involved, directly interacting with the Fed rates prediction
market. C is a massive insurance company whose price will be dictated
by the weighted sum of two components: their business operations
and their bond portfolio. For operations information, you will receive
their quarterly earnings news messages twice per day (at 22 and 88
seconds) as a baseline. But C ’s P/E ratio will NOT be constant; it will
be inversely proportional to the expected bond yields, since larger
bond yields make C less attractive to investors. The rough structure of
this relationship is given below where is a sensitivity constant and
and represent bond yields at time and.
```
```
γ yt
y 0 t 0
```
```
The bond portfolio will also inversely depend on the yields. For realism, we use a Taylor
expansion to characterize this relationship:
```
```
Here, represents the value of the bond portfolio, / represent duration and convexity
constants, and represents yields at time. Combining the two components gives the final
price for C,
```
```
where is the number of outstanding shares and is a weighting constant.
```
```
The Federal Reserve Prediction Market aims to predict what decision our hypothetical Fed will
make at the end of April in terms of interest rates. There are 3 possibilities, they will either hike
rates by 25 bps (basis points), keep rates the same, or cut rates by 25 bps. You will be provided
quoted probabilities for each outcome, and to help you trade, you will also receive structured
and unstructured news releases. The structured news will be in the form of Forecasted vs.
Actual CPI prints; Actual higher than Forecasted indicates inflation and points toward rate
hikes, and vice versa points toward cuts. The unstructured news will be in the form of news
headlines that may or may not relate to the Fed’s decision.
```
```
You should also use the quoted probabilities to calculate the expected policy change and
convert that into yields using the following formula:
```
_P_

```
op
t = EPSt ⋅ PEt , PEt = PE^0 ⋅ e
```
```
− γ ( yt − y 0 )
```
Δ _Bt_ ≈ _B_ 0 (− _D_ Δ _yt_ +

```
1
2
```
_C_ (Δ _yt_ )^2 )

```
B DC
yt t
```
_Pt_ = _EPSt_ ⋅ _PEt_ + _λ_

Δ _Bt_

_N_

+noise

_N λ_

_E_ [Δ _rt_ ]=(+ 25 ) _qt_ hike+ 0 ⋅ _qt_ hold+(− 25 ) _q_ c _t_ ut

_yt_ = _y_ 0 + _βyE_ [Δ _rt_ ]


##### |Education

ETFs are investment funds that are traded on stock exchanges, similar to individual stocks.
They hold a diversified portfolio of assets, such as stocks, bonds, or commodities, and are
designed to track the performance of a specific index or sector. In the real world, ETFs offer
investors a way to gain exposure to a broad range of assets without having to buy each
one individually, and they are known for their low cost.

In the context of ETFs, creation and redemption processes involve entering and unwinding
swap agreements to manage the ETF’s exposure to the underlying index. Here’s a succinct
description:

An ETF can be thought of as an agreement between parties on the exchange; one can
swap (for a small fee!) between 1 share of the ETF for 1 share of A, B and C each. Similarly,
one can make a trade in the opposite direction (once again, paying a small fee).

Meanwhile, an option is a contract that gives the buyer the right, but not the obligation, to
buy or sell an underlying asset at a fixed price, the strike price, on a specific expiration date.
A call option gives the right to buy, while a put option gives the right to sell. A European
option can only be exercised at expiration, unlike an American option which can be
exercised at any time.

More specifically, Put-Call Parity (PCP) is a foundational no-arbitrage relationship that
must hold between the prices of European calls and puts sharing the same strike and
expiry:

where and are the call and put prices, is the current spot price, is the strike, is
the risk-free rate, and is time to expiration. Intuitively, a long call and short put at the
same strike synthetically replicates a long forward position, so if the two sides of this
equation diverge, a riskless profit is available.

_C_ − _P_ = _S_ 0 − _Ke_

```
− rT
```
```
C P S K r
T
```
##### |Potential Strategies

```
Provide Liquidity: Offer continuous buy (bid) and sell (ask) quotes on the exchange to trade
with participants looking to take a position. Your bid quote should be less than your ask quote
(why is this?). Hint: think about the difference between your bid and your ask (known as the
spread) as payment for taking on risk.
```
```
Manage Risk: Monitor and manage your exposure to market movements and potential losses.
Note: strategies that produce consistent profits will score higher than those with a similar
expected return and higher variance.
```
```
Strategic Adaptation: Navigate a market that includes participants that are both more and less
informed that you. Consider which participants you want to trade against. Consider how trades
you see on the exchange (especially from what looks like smart money) should change your fair
value for assets.
```
```
Market Impact: Understand how your trades may affect market prices, in terms of pushing
prices up or down. Hint: think about how your market impact varies depending on the overall
market liquidity.
```
```
Understand News: How will news affect the prices of the underlying assets? Understand both
structured and unstructured news, and from this develop a quantitative model which
encapsulates your want to buy/sell. Fundamentally, every significant movement in the stock
market is driven by some amount of news, and some amount of noise.
```
```
Put-Call Parity: If quoted calls and puts are mispriced, violating the equation listed previously,
you can trade the mispriced leg against a synthetic replication of it using the other leg and the
underlying forward.
```
```
Box Spread: A box is constructed by combining a bull call spread and bear put spread at the
same two strikes. Its payoff should always be regardless of where the underlying
settles, so after taking the risk-free rate into account, if the box is quoted above or below the
theoretical present value, there exists arbitrage.
```
```
Note that different strategies will be more effective at different points of the competition. Make
sure to adapt your sub-strategies between rounds.
```
_K_ 1 < _K_ 2


##### |Round Specification & Scoring

Rounds: The competition will consist of 3 hours of rounds, each 15 minutes long, with
**increasing difficulty and increasingly weighted scoring**. Competitors are randomly
assigned to an exchange each round through a round robin process to trade against a
variety of other market participants.

**There will be a settlement price for all assets at the end of each round.** Each team’s P&L
will be calculated using these final prices and added to their cumulative total. Positions do
not carry over between rounds. The settlement price for the ETFs is computed as the NAV,
and everything is marked out to the fair value.

Difficulty will progress over the rounds, typically through opposing market makers
**increasing their spreads, decreasing volume** , and the underlying assets becoming more
volatile. Market makers in later rounds will be much quicker to respond to news than those
in early rounds. Moreover, strategies that generate positive P&L at the start of the
competition are expected to continue to generate positive P&L across rounds, but decrease
over time. As a result, we want to encourage competitors to adapt as the competition
progresses and edges become smaller, as later rounds become weighted more heavily.

We have a **nonlinear grading schema that converts P&L into points.** Strategies that
generate consistent positive P&L are expected to do much better than strategies that are
high risk/high reward in nature. Similarly, outlier results of both large positive and negative
P&L will likely not excessively impact a team’s total score. As such, a handful of terrible
rounds will not erase a team’s chance of overall success, nor will a handful of great rounds
assure it.

Note that the practice round will not count towards total points.

##### |Rules

```
You may take long or short positions in all assets available in a round, subject to risk limits
specified below. Exceeding these risk limits will result in the rejection of your entire order.
```
##### |Risk Limits

```
As a market maker, your firm stipulates the following risk assets across the tradable assets:
```
```
Max Order Size
Max Open Order (Size of unfilled order)
Outstanding Volume (Total volume of unfilled order)
Max Absolute Position (Sum of long and short positions)
```
```
If you exceed any of these positions, all further orders for that contract that exacerbate the risk
limits will be blocked by the exchange. Similarly, if any order is rejected by limit, participants are
not informed of which limit they breached. We will release the specific risk limits in a pinned
Ed post in advance of the competition.Risk limits are subject to change on the day of the
competition.
```

##### |Miscellaneous Tips

In order to be successful in this case (and as a market maker in general), you will need:

```
A market making algorithm that uses the predicted “fair value” to make profitable
trades:
There will be some “smart” and “dumb” money bots on the exchange. Which bots
should you want to trade against? Which bots should inform your future
expectations of price/settlement price?
Is it always reasonable to quote symmetrically (in size and/or price) about your
predicted fair price?
What modifications (if any) should you make in the way you trade if the fair price
predicted by your model deviates from the mid-price of the market? What if only one
side of your quotes gets filled?
When would it be justified to “cross the spread” to take a position?
Do you want to hold your positions to settlement? Does this expose you to
additional risk?
Do you want to pay to get out of risk?
```
```
ETF market making is a delicate task.
How can you identify arbitrage opportunities in ETF pricing in terms of redemption
and creation?
Make sure to factor in redemption/creation costs!
Should you always take both sides of the arbitrage opportunity?
Hint: When the ETF and equity prices don’t align, it’s more likely that the ETF is
mispriced.
If you’re willing to take on more variance, it may be the case that the arb exists
because the ETF is mispriced and the equity is fair, so if you do trade the arb, the
trade on equity may have been bad.
When and why would one decide to swap short?
```
```
Trading earnings is a difficult task. If you receive information before the rest of the
market does, where will you be willing to buy and sell? Does it always make sense to
post offers at the ‘new price’ or somewhere in between?
```
##### |Code Submission

```
Competitors will however run their bots from a box provided to you on the actual day of the
competition. Please make sure you know how to use VSCode and are able to SSH into a virtual
box— should questions arise, we will be there to answer them.
```
##### |Questions

```
For questions regarding Case 1, please post in the UChicago Trading Competition Ed in the
“case1” folder.
```
```
We will regularly check for new messages.
```

### CASE 2:

### Portfolio Optimization

##### |Introduction

```
Even as a child, playing in a kindergarten playground, you probably
remember looking up to the sky and thinking: how do allocators
construct a portfolio to maximize risk-adjusted returns? Fast-forward
several years, and you now have a chance to explore this question.
```
```
In this case, you are a portfolio analyst tasked with allocating a fund
across 25 assets grouped into five sectors. To aid you, your company
has provided you with five years of historical data for each asset. Your
goal is to develop an algorithm to construct and rebalance a portfolio
aimed at maximizing returns while simultaneously minimizing returns
variance.
```
```
You will be given a CSV with intraday prices (30 ticks per day). The
algorithm you submit will be evaluated on the twelve months
immediately after the period in the CSV.
```
##### |Education

```
SHARPE RATIO
```
```
Making money is good, but making it consistently is even better. The
“Sharpe Ratio” attempts to capture this distinction by measuring how
much return a portfolio earns per unit risk, and is given by
```
```
where denotes the daily returns. We use the Sharpe ratio of your
algorithm to compute your final score.
```
```
EQUAL WEIGHT PORTFOLIO
```
```
The naive portfolio allocation is giving each stock in your portfolio a
weighting of , assuming distinct assets. This method assumes
that your portfolio’s assets aren’t correlated with one another, and has
many downsides. However, it’s often a good baseline: if your approach
is underperforming the equal weight portfolio, something’s probably
gone wrong. Our starter code implements this approach for you.
```
Sharpe=√ 252 ⋅

mean( _rt_ )

std( _rt_ )

_rt_

1 / _n n_

```
MARKOWITZ
```
```
In 1952, Harry Markowitz of the University of Chicago published “Portfolio Selection,” which
formalized this basic intuition and formed the basis of modern portfolio theory (MPT).
Markowitz conceived of an efficient frontier of portfolios that maximize return given a certain
level of risk (or alternatively minimize risk given a certain desired return). Along this efficient
frontier, the actual portfolio chosen is based on the individual investor’s level of risk aversion
(high risk aversion = low level of risk, and vice versa). In the absence of exact knowledge on
risk aversion, portfolio managers can choose an efficient frontier portfolio based on the specific
objectives given to them by their clients or firms.
```
```
Efficient Frontier
```
```
0
```
```
5
```
```
10
```
```
15
```
```
20
```
```
25
```
```
Return
```
```
0 5 10 15 20 25 30 35
Standard Deviation (Risk)
```
```
10
```
```
12.
```
```
15
```
```
17.
```
```
20
```
```
To implement a Markowitz portfolio, investors need some estimate of expected return and an
estimate of risk. Additionally, we need some way to estimate the correlations, or covariances,
between the returns of the different assets. Intuitively, a portfolio where all asset returns are
highly correlated should have more risk than one with the same weights and risks for each
individual asset but where all the asset returns are uncorrelated due to the fact that in the
former case, one asset losing a large portion of its value means the entire portfolio likely will
while this is not true in the latter case. Indeed, we see that the variance of returns in a portfolio
(a measure of risk) is given by
```
```
where refers to the weight of the asset in the portfolio, refers to the return of that
asset, and refers to the number of assets in the portfolio. While we can reasonably estimate
variances and covariances of asset returns based on historical data, empirically, historical
returns have not been a strong predictor of future expected return and large covariance matrix
estimates tend to be numerically unstable, leading to practical difficulties in implementing a
Markowitz portfolio. Since the development of MPT, investors and researchers have looked for
new ways of dealing with this problem.
```
_σ_

```
2
p =
```
```
n
∑
i = 1
```
```
n
∑
j = 1
```
_wiwj_ Cov( _ri_ , _rj_ )

```
wi i th ri
n
```

A downside of the Markowitz approach is that it works best on longer time horizons.
Intraday price movements are often incredibly volatile and unpredictable— Markowitz
based implementations are best used for reallocating at a ‘macro’ level rather than trading
on a daily level.

**RISK PARITY ALLOCATION (RPA)**

One approach to portfolio allocation is to simply ignore expected returns when determining
how to allocate assets in a portfolio. The most naive way of doing this would be to simply
find the portfolio with the lowest predicted risk, but this would simply be to invest in a risk
free asset, and the return offered by such a portfolio would not entice many investors.
What we want instead is a way to have a portfolio full of risky assets where more weight is
given to those with lower risk. This can be done by ensuring that every asset’s risk
contribution to the portfolio is equal, meaning that less risky assets have more weight than
riskier ones in order to equalize the risk contribution. Here, risk contribution is calculated as:

where is the column vector of weights, and is the variance-covariance matrix from the
previous equation (not a summation of ). In this calculation, assets that are uncorrelated
with the rest of the portfolio contribute less to risk than correlated assets with the same
risk level, which is intuitive since a drop in the value of an uncorrelated asset does not
contribute as much to overall losses for the portfolio as would a correlated asset. As it has
been shown that asset volatilities and correlations are relatively stable over time, historical
risk contribution can be used as a reliable estimate for risk contribution.

**TOWARDS RETURN PREDICTION**

Portfolio construction determines how to translate expected returns into weights. But
where do the expected returns come from? In the simplest approaches, they are estimated
from historical averages, which are insensitive to underlying structural patterns common in
financial data. More sophisticated strategies attempt to forecast returns dynamically using
these patterns.

Asset returns often exhibit structure that makes this possible: momentum effects, mean
reversion, correlations across related assets, and clustering of volatility are all phenomena
that researchers have documented and exploited. In this case, the sector structure of the
universe is designed to reflect real economic relationships that influence how assets co-
move and how they diverge. Signals that exploit patterns within and across sectors, or that
identify periods when past returns are predictive of future ones, can compound into
meaningful edges even when individual forecasts are weak. The most successful teams
will go beyond static allocation and find ways to exploit predictable structure in the data.

_wi_ (Σ _w_ ) _i_

√ _wT_ Σ _w_

```
w Σ
w
```
##### |Case Specification & Rules

```
The exchange trading platform will not be used for this case. Teams are expected to develop
their strategies using our Python stub code and submit their code before the competition.
```
```
You will receive historical tick price data, a sector label, bid-ask spread (in basis points), and a
borrow cost (annualized cost of holding a short position, in basis points) for all 25 assets. On
each trading day, your algorithm will receive the price history observed so far and must submit
an allocation. These allocations will be in the form of weights on each asset: weights can be
positive, negative, or zero, but the sum of their absolute values must be at most one. If your
algorithm outputs weights that violate this constraint, the evaluator will rescale them
proportionally. Our starter code includes an example of how your code will be evaluated; this is
being provided to help you understand the evaluation process only, and should NOT be taken as
predictive of your final score – ignore this at your own peril!
```
```
Evaluation uses the full intraday price path rather than only daily closing prices. For each tick,
compute the portfolio return by summing, across all 25 assets, each portfolio weight multiplied
by that asset’s simple return derived from its log return. Short positions also pay a borrow
charge each tick equal to the sum, across assets, of the short exposure times the asset’s
annualized borrow cost times the fraction of a year represented by one tick. At the end of each
day, when the portfolio is rebalanced from old weights to new weights, transaction costs
depend on the change in each asset’s weight and its bid-ask spread. The total cost is the sum of
two parts: a linear term equal to half the spread times the absolute weight change, and a
quadratic term equal to 2.5 times the spread times the squared weight change. Our starter code
includes a reference evaluator so you can reproduce these mechanics locally.
```
```
You may use any packages, programming languages, and agents to study the training data we
provide, but the submitted portfolio code must be in Python and will be restricted in
dependencies. The default environment used to run submitted code will use Python 3.12 and
will only have NumPy, pandas, scikit-learn, and SciPy installed. More details will be published
on Ed and you will have the opportunity to request for additional packages.
```
```
We strongly advise that you test your submission using a similar environment on your local
machine before submitting your final code; submitted code that does not compile or that fails to
run for any time step will be disqualified for this case and the team that submitted it will receive
0 points. Before final submission, there will be an opportunity to test if your code compiles and
runs properly (note that the Sharpes yielded from this test will not be indicative of what your
actual Sharpe ratio will be).
```

##### |Scoring

Teams will be ranked based on their annual Sharpe ratio over the 12-month test period.

##### |Case Materials & Data

Python stub code and training data will be released along with the case packet and
additional supplementary resources through the UChicago Trading Competition Ed.

We are requiring the final code for this case to be submitted by **11:59 PM CST on
Thursday, April 9th**. Note that this is different from Case 1, as we will be computing the
results of this round prior to the competition. Code submitted past this deadline will not be
accepted, and we reserve the right to disqualify any competitors who submit incomplete
code or miss this deadline.

##### |Miscellaneous Tips

```
Analyze returns, not prices. Prices of stocks tend to be non-stationary processes, but
returns are generally stationary. Analyzing returns series will be more fruitful for your
strategies than analyzing price series.
```
```
Don’t test strategies on the same data you train them on. Strategies will likely perform
well on data your model has already seen – what’s relevant is how well the strategy
performs on data the model has not yet seen. You should not necessarily expect that
your strategy will perform as well out-of-sample as it will in-sample; holding out a
portion of your training data to test on (or running any other procedure to test on new
data) is strongly advisable to get a more accurate sense of how successful your
strategy will be. Every year, people overfit to the data – don’t let that be you!
```
```
Daily movement and intraday price changes are often two very different processes.
Make sure you understand what the dynamics of each of these are—portfolio
optimization is fundamentally about trading off short-term volatility with long-term
predictability.
Think carefully about costs. The scoring uses net returns after transaction costs and
borrow fees. A strategy that rebalances frequently needs to outperform a lower-
frequency one to justify the additional cost.
```
##### |Questions

```
For questions regarding Case 2, please post in the UChicago Trading Competition Ed with the
“Portfolio Optimization” tag.
```

