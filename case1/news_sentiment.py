"""
News Sentiment Classifier for Stock A

Keyword-based sentiment analysis for A-symbol news events.
Based on empirical findings from case1_stock_a_update_v2.md (Change 4).

Classification uses keyword matching to determine if news is bullish, bearish, or neutral.
Confidence score based on keyword density and uniqueness of signal.
"""

from typing import Tuple, Optional


# Keyword lists based on observed A-news events in practice rounds
# Strong bearish indicators
BEARISH_KEYWORDS = [
    "loses", "lost", "suffers", "suffering", "suffered", "fails", "hamper",
    "declining", "decline", "insolvency", "insolvent", "rival", "mismanagement",
    "recall", "lawsuit", "investigation", "investigated",
    "missed", "miss", "plunges", "plunge", "slips", "slipping",
    "fingers",  # as in "slips through A's fingers"
    "warns", "warning", "warned", "problem", "problems",
    "downgrade", "downgraded", "eroding", "eroded",
    "risk", "risks", "risky",
    # Added from empirical data analysis (runs 2 & 3)
    "ineffective", "inefficiency", "inefficient",
    "erode", "erosion",
    "competition", "competitive pressure",
    "faces", "facing", "hurdles",
    "rising costs", "operational costs",
    "flat", "fall flat", "falls flat"
]

# Strong bullish indicators
BULLISH_KEYWORDS = [
    "awarded", "award",
    "wins", "won", "winning",
    "alliance", "partner", "partnership",
    "expansion", "expand", "expanding", "bolster",
    "innovative", "innovation", "revolutionary",
    "reduce costs", "cost reduction",
    "record", "records",
    "boosts", "boost", "boosting",
    "beats", "beat", "beating",
    "breakthrough",
    "signed", "signs",
    "contract",  # unless "loses contract", handled by bearish
    # Added from empirical data analysis (runs 2 & 3)
    "praise", "praised",
    "earn", "earned", "earns",
    "increase", "increased", "increases", "increasing",
    "funding", "fund", "funded",
    "receives", "received", "receiving",
    "substantial", "substantially", "significant", "significantly",
    "profitability",
    "transparency", "accountability",
    "venture", "prominent",
    "reveal", "revealed"
]


class NewsSentimentClassifier:
    """
    Keyword-based sentiment classifier for A-symbol news.

    Returns sentiment direction and confidence score for trading decisions.
    Uses simple majority rule: whichever direction has more keyword hits wins.
    """

    def __init__(self):
        self.bearish_keywords = BEARISH_KEYWORDS
        self.bullish_keywords = BULLISH_KEYWORDS

    def classify(self, news_dict: dict) -> Tuple[str, float]:
        """
        Classify news sentiment based on keyword matching.

        Args:
            news_dict: News message dict with 'symbol' and 'content' keys

        Returns:
            (sentiment, confidence) where:
                sentiment in ["bullish", "bearish", "neutral", "unknown"]
                confidence in [0.0, 1.0]

        Logic:
            - Count bullish and bearish keyword hits
            - Pick whichever direction has MORE hits (no threshold)
            - Confidence = winning_hits / total_hits
            - If tied or no hits: return "neutral" or "unknown"
        """
        symbol = news_dict.get("symbol")
        content = news_dict.get("content", "")

        # Only classify A-symbol news
        if symbol != "A":
            return ("neutral", 0.0)

        # Skip if no content
        if not content:
            return ("unknown", 0.0)

        content_lower = content.lower()

        # Count keyword hits
        bearish_hits = sum(1 for kw in self.bearish_keywords if kw in content_lower)
        bullish_hits = sum(1 for kw in self.bullish_keywords if kw in content_lower)

        total_hits = bearish_hits + bullish_hits

        # No keywords matched
        if total_hits == 0:
            return ("unknown", 0.0)

        # Simple logic: pick whichever direction has more hits
        # Confidence = proportion of hits in the winning direction
        if bullish_hits > bearish_hits:
            confidence = bullish_hits / total_hits
            return ("bullish", confidence)
        elif bearish_hits > bullish_hits:
            confidence = bearish_hits / total_hits
            return ("bearish", confidence)
        else:
            # Tied - don't trade
            return ("neutral", 0.5)

    def classify_with_details(self, news_dict: dict) -> dict:
        """
        Extended classification with debugging info.

        Returns:
            {
                'sentiment': str,
                'confidence': float,
                'bearish_hits': int,
                'bullish_hits': int,
                'content_preview': str
            }
        """
        sentiment, confidence = self.classify(news_dict)

        content = news_dict.get("content", "")
        content_lower = content.lower()

        bearish_hits = sum(1 for kw in self.bearish_keywords if kw in content_lower)
        bullish_hits = sum(1 for kw in self.bullish_keywords if kw in content_lower)

        return {
            'sentiment': sentiment,
            'confidence': confidence,
            'bearish_hits': bearish_hits,
            'bullish_hits': bullish_hits,
            'content_preview': content[:100]
        }
