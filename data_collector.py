"""
Data Collector Bot for Stock A Price Analysis

Standalone observer bot that records tick-level prices, news events, and price reactions
for analyzing A's behavior and validating the constant-PE assumption.

Usage:
    python data_collector.py                    # Run collector (blocks until Ctrl+C)
    python data_collector.py --plot <session>   # Generate plots from session data
    python data_collector.py --analyze <session> # Run linear model analysis

Data is saved to case1/data/ directory with session timestamp.
"""

import asyncio
import time
from datetime import datetime
from typing import Optional, List, Dict
import csv
from pathlib import Path
import sys

from utcxchangelib import XChangeClient


class DataCollector(XChangeClient):
    """Observer bot that records all price and news data for A without trading."""

    def __init__(self, host: str, username: str, password: str):
        super().__init__(host, username, password, silent=False)

        # Data buffers
        self.tick_buffer: List[Dict] = []
        self.earnings_buffer: List[Dict] = []
        self.news_buffer: List[Dict] = []
        self.events_buffer: List[Dict] = []
        self.mid_samples: List[Dict] = []  # continuous 1s sampling

        # State tracking
        self.tick_counter = 0
        self.earnings_count = 0
        self.news_count = 0
        self.start_time = time.time()
        self.session_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        # Create data directory
        self.data_dir = Path(__file__).parent / "data"
        self.data_dir.mkdir(exist_ok=True)

        print(f"[COLLECTOR] Session: {self.session_id}")
        print(f"[COLLECTOR] Data directory: {self.data_dir}")

    async def bot_handle_book_update(self, symbol: str):
        """Record every book update for A."""
        if symbol != "A":
            return

        self.tick_counter += 1
        wall_time = time.time()

        book = self.order_books.get("A")
        if not book:
            return

        # Extract best bid/ask
        bids = [(px, qty) for px, qty in book.bids.items() if qty > 0]
        asks = [(px, qty) for px, qty in book.asks.items() if qty > 0]

        best_bid = max(bids, key=lambda x: x[0])[0] if bids else None
        best_ask = min(asks, key=lambda x: x[0])[0] if asks else None
        bid_size = max(bids, key=lambda x: x[0])[1] if bids else None
        ask_size = min(asks, key=lambda x: x[0])[1] if asks else None

        mid = (best_bid + best_ask) / 2 if best_bid and best_ask else None
        spread = best_ask - best_bid if best_bid and best_ask else None

        # Record tick
        self.tick_buffer.append({
            'tick_num': self.tick_counter,
            'wall_time_s': wall_time,
            'symbol': 'A',
            'best_bid': best_bid,
            'best_ask': best_ask,
            'mid': mid,
            'bid_size': bid_size,
            'ask_size': ask_size,
            'spread': spread
        })

        # Also add to events (but don't spam - sample every 10th tick)
        if self.tick_counter % 10 == 0:
            self.events_buffer.append({
                'wall_time_s': wall_time,
                'event_type': 'TICK',
                'symbol': 'A',
                'details': f"bid={best_bid} ask={best_ask}",
                'a_mid': mid
            })

    async def bot_handle_news(self, news_release: dict):
        """Record all news events and trigger sampling windows."""
        news_type = news_release["kind"]
        news_data = news_release["new_data"]
        tick = news_release.get("tick", self.tick_counter)
        wall_time = time.time()
        symbol = news_release.get("symbol")

        # Get current A mid
        a_mid = self._get_a_mid()

        if news_type == "structured":
            subtype = news_data.get("structured_subtype")

            if subtype == "earnings":
                asset = news_data.get("asset")
                eps = news_data.get("value")

                if asset == "A":
                    self.earnings_count += 1
                    print(f"\n[EARNINGS #{self.earnings_count}] A: EPS={eps:.4f}, pre_mid={a_mid}, tick={tick}")

                    # Trigger 15s sampling window
                    asyncio.create_task(self._sample_earnings_window(tick, wall_time, eps, a_mid))

                    # Add to events
                    self.events_buffer.append({
                        'wall_time_s': wall_time,
                        'event_type': 'EARNINGS',
                        'symbol': 'A',
                        'details': f"eps={eps:.4f}",
                        'a_mid': a_mid
                    })

            elif subtype == "cpi_print":
                # User requested to skip CPI
                pass

        else:  # unstructured
            content = news_data.get("content", "")

            # Filter out fedspeak/CPI mentions (user requested)
            content_lower = content.lower()
            if "fed" in content_lower or "cpi" in content_lower or "federal reserve" in content_lower:
                return

            # User said all other unstructured news is for A
            self.news_count += 1
            print(f"\n[NEWS #{self.news_count}] {content[:60]}... pre_mid={a_mid}, tick={tick}")

            # Trigger 10s sampling window
            asyncio.create_task(self._sample_news_window(tick, wall_time, content, symbol, a_mid))

            # Add to events
            self.events_buffer.append({
                'wall_time_s': wall_time,
                'event_type': 'NEWS',
                'symbol': symbol or 'None',
                'details': content[:100],
                'a_mid': a_mid
            })

    def _get_a_mid(self) -> Optional[float]:
        """Get current mid price for A."""
        book = self.order_books.get("A")
        if not book:
            return None

        bids = [px for px, qty in book.bids.items() if qty > 0]
        asks = [px for px, qty in book.asks.items() if qty > 0]

        if bids and asks:
            return (max(bids) + min(asks)) / 2
        return None

    async def _sample_earnings_window(self, tick: int, start_time: float, eps: float, pre_mid: Optional[float]):
        """Sample A mid at +1s, +2s, +5s, +10s, +15s after earnings."""
        samples = {}

        for delay_s in [1, 2, 5, 10, 15]:
            await asyncio.sleep(delay_s - max(samples.keys(), default=0))
            samples[delay_s] = self._get_a_mid()

        self.earnings_buffer.append({
            'earnings_tick': tick,
            'wall_time_s': start_time,
            'eps': eps,
            'pre_mid': pre_mid,
            'mid_1s': samples.get(1),
            'mid_2s': samples.get(2),
            'mid_5s': samples.get(5),
            'mid_10s': samples.get(10),
            'mid_15s': samples.get(15),
            'settled_mid': samples.get(15)
        })

        print(f"[EARNINGS WINDOW] settled_mid={samples.get(15)}")

    async def _sample_news_window(self, tick: int, start_time: float, content: str, symbol: Optional[str], pre_mid: Optional[float]):
        """Sample A mid at +1s, +3s, +5s, +10s after news."""
        samples = {}

        for delay_s in [1, 3, 5, 10]:
            await asyncio.sleep(delay_s - max(samples.keys(), default=0))
            samples[delay_s] = self._get_a_mid()

        self.news_buffer.append({
            'news_tick': tick,
            'wall_time_s': start_time,
            'news_type': 'UNSTRUCTURED',
            'symbol': symbol or 'None',
            'content_or_value': content,
            'pre_mid': pre_mid,
            'mid_1s': samples.get(1),
            'mid_3s': samples.get(3),
            'mid_5s': samples.get(5),
            'mid_10s': samples.get(10)
        })

    async def continuous_mid_sampler(self):
        """Sample A mid every second for continuous price tracking."""
        while True:
            await asyncio.sleep(1)
            wall_time = time.time()
            mid = self._get_a_mid()

            self.mid_samples.append({
                'wall_time_s': wall_time,
                'mid': mid
            })

    async def periodic_flush(self):
        """Flush buffers to CSV every 30s and print status."""
        while True:
            await asyncio.sleep(30)
            self._flush_all()

            elapsed = time.time() - self.start_time
            print(f"\n[STATUS] Elapsed: {elapsed:.0f}s | Ticks: {len(self.tick_buffer)} | Earnings: {self.earnings_count} | News: {self.news_count}")

    def _flush_all(self):
        """Write all buffers to CSV files."""
        # Ticks
        if self.tick_buffer:
            tick_file = self.data_dir / f"ticks_{self.session_id}.csv"
            file_exists = tick_file.exists()
            with open(tick_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'tick_num', 'wall_time_s', 'symbol', 'best_bid', 'best_ask',
                    'mid', 'bid_size', 'ask_size', 'spread'
                ])
                if not file_exists:
                    writer.writeheader()
                writer.writerows(self.tick_buffer)
            self.tick_buffer = []

        # Earnings
        if self.earnings_buffer:
            earnings_file = self.data_dir / f"earnings_{self.session_id}.csv"
            file_exists = earnings_file.exists()
            with open(earnings_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'earnings_tick', 'wall_time_s', 'eps', 'pre_mid',
                    'mid_1s', 'mid_2s', 'mid_5s', 'mid_10s', 'mid_15s', 'settled_mid'
                ])
                if not file_exists:
                    writer.writeheader()
                writer.writerows(self.earnings_buffer)
            self.earnings_buffer = []

        # News
        if self.news_buffer:
            news_file = self.data_dir / f"news_{self.session_id}.csv"
            file_exists = news_file.exists()
            with open(news_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'news_tick', 'wall_time_s', 'news_type', 'symbol',
                    'content_or_value', 'pre_mid', 'mid_1s', 'mid_3s', 'mid_5s', 'mid_10s'
                ])
                if not file_exists:
                    writer.writeheader()
                writer.writerows(self.news_buffer)
            self.news_buffer = []

        # Events (combined log)
        if self.events_buffer:
            events_file = self.data_dir / f"events_{self.session_id}.csv"
            file_exists = events_file.exists()
            with open(events_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'wall_time_s', 'event_type', 'symbol', 'details', 'a_mid'
                ])
                if not file_exists:
                    writer.writeheader()
                writer.writerows(self.events_buffer)
            self.events_buffer = []

        # Continuous mid samples
        if self.mid_samples:
            mid_file = self.data_dir / f"mid_samples_{self.session_id}.csv"
            file_exists = mid_file.exists()
            with open(mid_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['wall_time_s', 'mid'])
                if not file_exists:
                    writer.writeheader()
                writer.writerows(self.mid_samples)
            self.mid_samples = []

    async def start(self):
        """Start the collector with all background tasks."""
        print(f"[COLLECTOR] Starting data collection...")
        print(f"[COLLECTOR] Press Ctrl+C to stop and save data")

        # Start background tasks
        asyncio.create_task(self.periodic_flush())
        asyncio.create_task(self.continuous_mid_sampler())

        try:
            await self.connect()
        except KeyboardInterrupt:
            print("\n[COLLECTOR] Stopping... flushing final data")
            self._flush_all()
            print(f"[COLLECTOR] Data saved to {self.data_dir}")


# ============================================================================
# ANALYSIS FUNCTIONS (run separately after data collection)
# ============================================================================

def plot_session(session_id: str):
    """
    Generate matplotlib plot of A's price with news event markers.

    Args:
        session_id: Session timestamp (e.g., "2026-04-09_11-30-45")
    """
    try:
        import pandas as pd
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("ERROR: matplotlib or pandas not installed")
        return

    data_dir = Path(__file__).parent / "data"

    # Load data
    ticks_file = data_dir / f"ticks_{session_id}.csv"
    earnings_file = data_dir / f"earnings_{session_id}.csv"
    news_file = data_dir / f"news_{session_id}.csv"
    mid_file = data_dir / f"mid_samples_{session_id}.csv"

    if not ticks_file.exists():
        print(f"ERROR: No data found for session {session_id}")
        return

    # Read tick data
    ticks = pd.read_csv(ticks_file)
    ticks['datetime'] = pd.to_datetime(ticks['wall_time_s'], unit='s')

    # Read continuous mid samples if available
    if mid_file.exists():
        mid_samples = pd.read_csv(mid_file)
        mid_samples['datetime'] = pd.to_datetime(mid_samples['wall_time_s'], unit='s')
    else:
        mid_samples = None

    # Read events
    earnings = pd.read_csv(earnings_file) if earnings_file.exists() else pd.DataFrame()
    news = pd.read_csv(news_file) if news_file.exists() else pd.DataFrame()

    if not earnings.empty:
        earnings['datetime'] = pd.to_datetime(earnings['wall_time_s'], unit='s')
    if not news.empty:
        news['datetime'] = pd.to_datetime(news['wall_time_s'], unit='s')

    # Create figure
    fig, ax = plt.subplots(figsize=(16, 8))

    # Plot mid price (use continuous samples if available, else tick data)
    if mid_samples is not None and not mid_samples.empty:
        ax.plot(mid_samples['datetime'], mid_samples['mid'],
                'b-', linewidth=0.8, alpha=0.7, label='Mid Price (1s samples)')
    else:
        ax.plot(ticks['datetime'], ticks['mid'],
                'b-', linewidth=0.8, alpha=0.7, label='Mid Price')

    # Mark earnings events
    if not earnings.empty:
        for _, row in earnings.iterrows():
            ax.axvline(row['datetime'], color='green', alpha=0.5, linestyle='--', linewidth=1)
            # Annotate with EPS
            y_pos = row['settled_mid'] if pd.notna(row['settled_mid']) else ticks['mid'].mean()
            ax.text(row['datetime'], y_pos, f"EPS={row['eps']:.3f}",
                   rotation=90, va='bottom', fontsize=8, color='green')

    # Mark news events
    if not news.empty:
        for _, row in news.iterrows():
            ax.axvline(row['datetime'], color='orange', alpha=0.3, linestyle=':', linewidth=1)

    # Formatting
    ax.set_xlabel('Time')
    ax.set_ylabel('A Mid Price')
    ax.set_title(f'Stock A Price History - Session {session_id}')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Format x-axis
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    plt.xticks(rotation=45)

    # Save figure
    output_file = data_dir / f"plot_{session_id}.png"
    plt.tight_layout()
    plt.savefig(output_file, dpi=150)
    print(f"[PLOT] Saved to {output_file}")
    plt.close()


def analyze_linear_model(session_id: str):
    """
    Fit linear model: settled_mid = a + b × EPS
    Compare against naive PE model.

    Args:
        session_id: Session timestamp
    """
    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        print("ERROR: pandas or numpy not installed")
        return

    data_dir = Path(__file__).parent / "data"
    earnings_file = data_dir / f"earnings_{session_id}.csv"

    if not earnings_file.exists():
        print(f"ERROR: No earnings data for session {session_id}")
        return

    # Load earnings data
    df = pd.read_csv(earnings_file)

    # Filter out rows with missing settled_mid
    df = df.dropna(subset=['eps', 'settled_mid'])

    if len(df) < 2:
        print(f"ERROR: Need at least 2 earnings events with settled prices, got {len(df)}")
        return

    print(f"\n{'='*70}")
    print(f"LINEAR MODEL ANALYSIS - Session {session_id}")
    print(f"{'='*70}")
    print(f"Earnings events: {len(df)}")

    # Fit linear model: settled_mid = a + b × EPS
    X = df['eps'].values
    y = df['settled_mid'].values

    # Add constant term for intercept
    X_with_const = np.column_stack([np.ones(len(X)), X])

    # Least squares fit
    coeffs, residuals, rank, s = np.linalg.lstsq(X_with_const, y, rcond=None)
    a, b = coeffs

    # Predictions
    y_pred = a + b * X

    # R-squared
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    ss_res = np.sum((y - y_pred) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    # Naive PE model: PE = first_settled / first_EPS
    naive_pe = df.iloc[0]['settled_mid'] / df.iloc[0]['eps']
    y_naive = X * naive_pe

    # Naive R-squared
    ss_res_naive = np.sum((y - y_naive) ** 2)
    r_squared_naive = 1 - (ss_res_naive / ss_tot) if ss_tot > 0 else 0

    print(f"\nLINEAR MODEL: settled_mid = {a:.2f} + {b:.2f} × EPS")
    print(f"R² = {r_squared:.4f}")
    print(f"\nNAIVE MODEL: settled_mid = {naive_pe:.2f} × EPS  (PE from first earnings)")
    print(f"R² = {r_squared_naive:.4f}")

    print(f"\n{'EPS':<12} {'Actual':<12} {'Linear Pred':<12} {'Linear Resid':<12} {'Naive Pred':<12} {'Naive Resid':<12}")
    print(f"{'-'*12} {'-'*12} {'-'*12} {'-'*12} {'-'*12} {'-'*12}")

    for i, row in df.iterrows():
        eps_val = row['eps']
        actual = row['settled_mid']
        linear_pred = a + b * eps_val
        linear_resid = actual - linear_pred
        naive_pred = eps_val * naive_pe
        naive_resid = actual - naive_pred

        print(f"{eps_val:<12.4f} {actual:<12.2f} {linear_pred:<12.2f} {linear_resid:<+12.2f} {naive_pred:<12.2f} {naive_resid:<+12.2f}")

    # Summary stats
    print(f"\nLinear model RMSE: {np.sqrt(np.mean((y - y_pred)**2)):.2f}")
    print(f"Naive model RMSE:  {np.sqrt(np.mean((y - y_naive)**2)):.2f}")

    if r_squared > r_squared_naive:
        improvement = ((r_squared - r_squared_naive) / r_squared_naive * 100) if r_squared_naive > 0 else float('inf')
        print(f"\n✓ Linear model is {improvement:.1f}% better than naive PE model")
    else:
        print(f"\n✗ Naive PE model is better (constant PE assumption holds)")

    print(f"{'='*70}\n")


# ============================================================================
# MAIN
# ============================================================================

async def main():
    """Run data collector."""
    collector = DataCollector(
        "practice.uchicago.exchange:3333",
        "maryland_uiuc",
        "torch-karma-beacon"
    )
    await collector.start()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "--plot" and len(sys.argv) > 2:
            session_id = sys.argv[2]
            plot_session(session_id)

        elif command == "--analyze" and len(sys.argv) > 2:
            session_id = sys.argv[2]
            analyze_linear_model(session_id)

        elif command == "--help":
            print(__doc__)

        else:
            print("Usage:")
            print("  python data_collector.py                    # Run collector")
            print("  python data_collector.py --plot <session>   # Generate plots")
            print("  python data_collector.py --analyze <session> # Run analysis")
            print("  python data_collector.py --help             # Show help")

    else:
        # Run collector
        asyncio.run(main())
