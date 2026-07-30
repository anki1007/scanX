"""
STDRL trading environment and agent training. Imports torch ONLY when called.

Split from scripts/refresh_stdrl.py so the bake script stays importable and
testable on a machine with no torch: everything here is behind a lazy import,
and `make_env` works on numpy alone for the environment tests.

Three deliberate departures from the notebook this is based on, each of which
changes which agent wins:

TRANSACTION COSTS ARE CHARGED. The original environment executed every trade
free. The stated objective was "include transaction costs for realistic
simulations", and without them the optimal policy an RL agent discovers is to
churn — it collects the noise and pays nothing for it. Cost is applied to the
traded notional on BOTH sides.

REWARD IS THE CHANGE IN NET WORTH, NOT THE LEVEL. The original used
`reward = net_worth - initial_balance`, which pays the agent at every step for
wealth it already had. A step that loses money still returns a large positive
reward as long as the book is up overall, so the gradient barely reflects the
decision just taken.

CASH CANNOT GO NEGATIVE. The original computed `int(balance * action / price)`
per ticker in a loop, each using the balance BEFORE that loop's earlier buys had
been deducted... which is fine, but nothing stopped a buy when balance was
already spent. Orders are sized against remaining cash.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

import numpy as np

log = logging.getLogger("technofunda.rlenv")

__all__ = ["make_env", "train_and_evaluate", "buy_and_hold", "split_basket",
           "AGENTS", "PortfolioEnv"]

#: Only the continuous-action algorithms are meaningful here: the action is a
#: per-ticker weight in [-1, 1], not a discrete buy/sell/hold.
AGENTS = ("PPO", "A2C", "DDPG", "TD3", "SAC")

INITIAL_BALANCE = 100_000.0


def _matrix(basket: Mapping[str, Sequence[Mapping]]) -> tuple[list[str], np.ndarray]:
    """{code: [{close,...}]} -> (codes, closes[T, N]) aligned on the SHORTEST history.

    Aligned from the END, so every ticker contributes its most recent periods
    rather than its oldest — a 30-year name and a 3-year name should overlap on
    the recent window they share, not on unrelated decades.
    """
    codes = sorted(k for k, v in (basket or {}).items() if v)
    if not codes:
        return [], np.zeros((0, 0))
    n = min(len(basket[c]) for c in codes)
    cols = []
    for c in codes:
        tail = list(basket[c])[-n:]
        cols.append([float(r.get("close") or 0.0) for r in tail])
    return codes, np.asarray(cols, dtype=np.float64).T      # [T, N]


class PortfolioEnv:
    """Long/short-flat portfolio over N tickers. gymnasium.Env when available."""

    def __init__(self, basket, cost_bps: float = 25.0, initial_balance: float = INITIAL_BALANCE):
        self.codes, self.closes = _matrix(basket)
        if not self.codes or self.closes.shape[0] < 3:
            raise ValueError("basket has too little aligned history to trade")
        self.n = len(self.codes)
        self.T = self.closes.shape[0]
        self.cost = float(cost_bps) / 10_000.0
        self.initial_balance = float(initial_balance)
        self.max_steps = self.T - 1
        self._build_spaces()
        self.reset()

    def _build_spaces(self):
        try:
            from gymnasium import spaces
        except Exception:  # noqa: BLE001 - tests exercise the maths without gymnasium
            self.action_space = self.observation_space = None
            return
        self.action_space = spaces.Box(-1.0, 1.0, shape=(self.n,), dtype=np.float32)
        # per-ticker: price, 1-period return, position; plus cash and net worth
        self.observation_space = spaces.Box(-np.inf, np.inf,
                                            shape=(self.n * 3 + 2,), dtype=np.float32)

    # ------------------------------------------------------------------ gym API
    def reset(self, seed=None, options=None):
        if seed is not None:
            np.random.seed(seed)
        self.step_i = 0
        self.cash = self.initial_balance
        self.shares = np.zeros(self.n)
        self.net_worth = self.initial_balance
        self.prev_net_worth = self.initial_balance
        self.costs_paid = 0.0
        self.trades = 0
        return self._obs(), {}

    def _obs(self):
        i = min(self.step_i, self.T - 1)
        price = self.closes[i]
        prev = self.closes[max(i - 1, 0)]
        with np.errstate(divide="ignore", invalid="ignore"):
            ret = np.where(prev > 0, price / prev - 1.0, 0.0)
        value = self.shares * price
        pos = value / self.net_worth if self.net_worth > 0 else np.zeros(self.n)
        obs = np.concatenate([price, ret, pos,
                              [self.cash / self.initial_balance,
                               self.net_worth / self.initial_balance]])
        return np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    def step(self, action):
        action = np.nan_to_num(np.asarray(action, dtype=np.float64).ravel(), nan=0.0)
        action = np.clip(action, -1.0, 1.0)[: self.n]
        self.step_i += 1
        done = self.step_i >= self.max_steps
        price = self.closes[min(self.step_i, self.T - 1)]

        for k in range(self.n):
            p = price[k]
            if p <= 0:
                continue
            a = action[k]
            if a > 0:                                   # buy with a share of FREE cash
                spend = min(self.cash, self.cash * a)
                qty = int(spend / (p * (1 + self.cost)))
                if qty > 0:
                    gross = qty * p
                    fee = gross * self.cost
                    self.cash -= gross + fee
                    self.shares[k] += qty
                    self.costs_paid += fee
                    self.trades += 1
            elif a < 0:                                 # sell a share of the holding
                qty = int(self.shares[k] * abs(a))
                if qty > 0:
                    gross = qty * p
                    fee = gross * self.cost
                    self.cash += gross - fee
                    self.shares[k] -= qty
                    self.costs_paid += fee
                    self.trades += 1

        self.prev_net_worth = self.net_worth
        self.net_worth = float(self.cash + np.sum(self.shares * price))
        # the CHANGE, scaled — not the level. See the module docstring.
        reward = (self.net_worth - self.prev_net_worth) / self.initial_balance
        if self.net_worth <= 0:
            done = True
        return self._obs(), float(reward), bool(done), False, {"net_worth": self.net_worth}


def make_env(basket, cost_bps: float = 25.0):
    """A gymnasium-compatible env over the basket. Raises if gymnasium is absent."""
    import gymnasium as gym

    # PortfolioEnv FIRST. With gym.Env first in the MRO its no-op reset()/step()
    # shadow ours -- reset() then returns None and every agent dies with
    # "cannot unpack non-iterable NoneType object" before it trains a single step.
    class _Env(PortfolioEnv, gym.Env):
        metadata = {"render_modes": []}

        def __init__(self):
            PortfolioEnv.__init__(self, basket, cost_bps=cost_bps)

    return _Env()


def split_basket(basket, train_frac: float = 0.7):
    """(train, test) split on TIME, oldest-first. PURE.

    Without this the agents are trained and scored on the same bars, which
    measures memorisation rather than skill — a first run scored PPO at
    +11,742% that way. The split is chronological, never random: shuffling
    market data lets the model see the future.
    """
    train, test = {}, {}
    for code, rows in (basket or {}).items():
        rows = list(rows)
        cut = int(len(rows) * train_frac)
        if cut >= 12 and len(rows) - cut >= 12:      # a year each side, minimum
            train[code], test[code] = rows[:cut], rows[cut:]
    return train, test


def buy_and_hold(basket, cost_bps: float = 25.0) -> list:
    """Equity curve for an equal-weight buy-and-hold book. PURE-ish.

    THE BENCHMARK EVERY AGENT MUST BEAT. Indian large caps compounded hard over
    this window, so an agent that simply stays invested posts a huge return that
    says nothing about the policy. Published alongside the agents so a reader can
    see whether the model added anything at all.
    """
    env = PortfolioEnv(basket, cost_bps=cost_bps)
    env.reset()
    weight = 1.0 / max(env.n, 1)
    curve = [env.net_worth]
    first = True
    while True:
        action = np.full(env.n, weight if first else 0.0)
        first = False
        _, _, done, _, info = env.step(action)
        curve.append(info["net_worth"])
        if done:
            break
    return curve


def train_and_evaluate(basket, *, agents=AGENTS, timesteps: int = 20_000,
                       cost_bps: float = 25.0, train_frac: float = 0.7) -> tuple[dict, list]:
    """{agent: OUT-OF-SAMPLE equity curve} plus the list trained. Needs torch.

    Trains on the first `train_frac` of the months and evaluates on the rest, so
    every published number is out of sample. A "Buy & hold" curve over the same
    test window is included as the benchmark.
    """
    from stable_baselines3 import A2C, DDPG, PPO, SAC, TD3
    from stable_baselines3.common.vec_env import DummyVecEnv

    train, test = split_basket(basket, train_frac)
    if not train or not test:
        log.warning("basket too short to split; nothing evaluated out of sample")
        return {}, []
    print(f"[stdrl] train {min(len(v) for v in train.values())} months / "
          f"test {min(len(v) for v in test.values())} months (out of sample)")

    ctors = {"PPO": PPO, "A2C": A2C, "DDPG": DDPG, "TD3": TD3, "SAC": SAC}
    results, trained = {}, []
    for name in agents:
        ctor = ctors.get(str(name).upper())
        if ctor is None:
            log.warning("unknown agent %s - skipped", name)
            continue
        try:
            venv = DummyVecEnv([lambda: make_env(train, cost_bps)])
            model = ctor("MlpPolicy", venv, verbose=0)
            model.learn(total_timesteps=timesteps)

            # Evaluate on ONE full episode. The original looped a fixed 1,000
            # steps and reset whenever the episode ended, so the recorded curve
            # spliced several runs together and its mean/deviation described a
            # sawtooth rather than a portfolio.
            env = make_env(test, cost_bps)      # OUT OF SAMPLE
            obs, _ = env.reset()
            curve = [env.net_worth]
            while True:
                act, _ = model.predict(obs, deterministic=True)
                obs, _, done, _, info = env.step(act)
                curve.append(info["net_worth"])
                if done:
                    break
            results[str(name).upper()] = curve
            trained.append(str(name).upper())
            print(f"[stdrl] {name}: {len(curve)} steps, end {curve[-1]:,.0f}, "
                  f"costs {env.costs_paid:,.0f} over {env.trades} trades")
        except Exception as exc:  # noqa: BLE001 - one bad agent must not kill the board
            log.warning("agent %s failed: %s: %s", name, type(exc).__name__, exc)
    if results:
        try:
            results["Buy & hold"] = buy_and_hold(test, cost_bps)
            print(f"[stdrl] Buy & hold benchmark: end {results['Buy & hold'][-1]:,.0f}")
        except Exception as exc:  # noqa: BLE001
            log.warning("benchmark failed: %s", type(exc).__name__)
    return results, trained
