"""
Pydantic models for API response validation.

Validates data from the Gamma API, NewsAPI, and Reddit before it enters
the analysis pipeline. Prevents malformed or malicious data from causing
incorrect trading decisions, crashes, or unexpected behavior.

Addresses: HIGH-02 (API report), H-4 (security audit), S-03 (python analysis)
"""

import json
from pydantic import BaseModel, field_validator, model_validator
from typing import Optional


class GammaMarket(BaseModel):
    """Validated market from the Polymarket Gamma API.

    Ensures all fields used downstream (price, volume, liquidity, token IDs)
    are present and within valid ranges before processing.
    """
    id: str = ""
    condition_id: str = ""
    question: str = ""
    volume: float = 0.0
    liquidity: float = 0.0
    outcomePrices: Optional[str] = None
    clobTokenIds: Optional[str] = None
    end_date_iso: Optional[str] = None
    end_date: Optional[str] = None
    active: bool = True
    description: Optional[str] = None

    # Pass through any extra fields from the API (tokens, bestAsk, etc.)
    model_config = {"extra": "allow"}

    @field_validator("volume", "liquidity", mode="before")
    @classmethod
    def coerce_numeric(cls, v):
        """Handle None, empty strings, and NaN values from the API."""
        if v is None or v == "":
            return 0.0
        try:
            val = float(v)
            # NaN check: NaN != NaN
            if val != val:
                return 0.0
            return val
        except (ValueError, TypeError):
            return 0.0

    @field_validator("outcomePrices", mode="before")
    @classmethod
    def validate_prices(cls, v):
        """Validate that outcomePrices contains valid probabilities (0 to 1)."""
        if v is None or v == "":
            return None
        if isinstance(v, str):
            try:
                prices = json.loads(v)
                for p in prices:
                    pf = float(p)
                    if pf < 0.0 or pf > 1.0:
                        raise ValueError(f"Price out of valid range [0, 1]: {pf}")
                return v  # Return original string (downstream code parses it)
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                raise ValueError(f"Invalid outcomePrices: {e}")
        return v

    @field_validator("clobTokenIds", mode="before")
    @classmethod
    def validate_token_ids(cls, v):
        """Validate that clobTokenIds is valid JSON with string IDs."""
        if v is None or v == "":
            return None
        if isinstance(v, str):
            try:
                ids = json.loads(v)
                if not isinstance(ids, list):
                    raise ValueError("clobTokenIds must be a JSON array")
                for token_id in ids:
                    if not isinstance(token_id, str):
                        raise ValueError(f"Token ID must be string, got {type(token_id)}")
                return v
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid clobTokenIds JSON: {e}")
        return v

    @model_validator(mode="after")
    def ensure_has_id(self):
        """Ensure the market has at least one form of identifier."""
        if not self.id and not self.condition_id:
            raise ValueError("Market must have 'id' or 'condition_id'")
        return self


class NewsArticle(BaseModel):
    """Validated news article from NewsAPI."""
    title: str = ""
    source: str = "Unknown"
    description: str = ""
    content: str = ""
    url: str = ""
    published_at: str = ""

    @field_validator("title", "description", "content", mode="before")
    @classmethod
    def coerce_string(cls, v):
        """Handle None values from the API."""
        if v is None:
            return ""
        return str(v)


class RedditPost(BaseModel):
    """Validated Reddit post from PRAW."""
    title: str = ""
    text: str = ""
    score: int = 0
    num_comments: int = 0
    subreddit: str = ""
    url: str = ""
    created_utc: float = 0.0

    @field_validator("title", "text", mode="before")
    @classmethod
    def coerce_string(cls, v):
        """Handle None values from PRAW."""
        if v is None:
            return ""
        return str(v)

    @field_validator("score", "num_comments", mode="before")
    @classmethod
    def coerce_int(cls, v):
        """Handle None or non-integer values."""
        if v is None:
            return 0
        try:
            return int(v)
        except (ValueError, TypeError):
            return 0

    @field_validator("created_utc", mode="before")
    @classmethod
    def coerce_float(cls, v):
        """Handle None or non-numeric timestamps."""
        if v is None:
            return 0.0
        try:
            val = float(v)
            return 0.0 if val != val else val  # NaN check
        except (ValueError, TypeError):
            return 0.0
