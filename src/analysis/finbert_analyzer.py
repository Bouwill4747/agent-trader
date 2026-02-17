"""
FinBERT sentiment analyzer — scores text as positive, negative, or neutral.
Uses the ProsusAI/finbert model from Hugging Face, specifically trained on financial text.
"""

from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

from src.utils.logger import setup_logger

logger = setup_logger("finbert_analyzer")


class FinBERTAnalyzer:
    """Scores financial text sentiment using FinBERT."""

    # Pin model to a specific revision for supply chain security (H-5).
    # This prevents a compromised HuggingFace account from delivering
    # backdoored weights via a model update.
    MODEL_NAME = "ProsusAI/finbert"
    MODEL_REVISION = "4556d13015211d73dccd3fdd39d39232506f3e43"

    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.labels = ["positive", "negative", "neutral"]
        self._loaded = False

    def load_model(self):
        """Load FinBERT model and tokenizer from Hugging Face.

        Called lazily on first use — the model is ~400MB and takes
        a few seconds to download the first time.

        Security: pinned to a specific revision hash and trust_remote_code=False
        to prevent supply chain attacks via model updates.
        """
        if self._loaded:
            return

        try:
            logger.info("Loading FinBERT model (first time may download ~400MB)...")
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.MODEL_NAME,
                revision=self.MODEL_REVISION,
                trust_remote_code=False,
            )
            self.model = AutoModelForSequenceClassification.from_pretrained(
                self.MODEL_NAME,
                revision=self.MODEL_REVISION,
                trust_remote_code=False,
            )
            self.model.eval()  # Set to evaluation mode (disables dropout)
            self._loaded = True
            logger.info("FinBERT model loaded successfully (revision: %s)", self.MODEL_REVISION[:12])
        except Exception as e:
            logger.error("Failed to load FinBERT: %s", e)
            raise

    def analyze_text(self, text: str) -> dict:
        """Score a single text for sentiment.

        Args:
            text: The text to analyze (news headline, article, Reddit post)

        Returns:
            Dict with keys: label, positive, negative, neutral, confidence
            Example: {"label": "positive", "positive": 0.85, "negative": 0.05,
                      "neutral": 0.10, "confidence": 0.85}
        """
        self.load_model()

        # Truncate long text — FinBERT has a 512 token limit
        tokens = self.tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )

        # Run inference (no gradient computation needed)
        with torch.no_grad():
            outputs = self.model(**tokens)
            probabilities = torch.softmax(outputs.logits, dim=1)[0]

        # Map probabilities to labels
        scores = {
            label: prob.item()
            for label, prob in zip(self.labels, probabilities)
        }

        # Determine the winning label
        best_label = max(scores, key=scores.get)

        return {
            "label": best_label,
            "positive": scores["positive"],
            "negative": scores["negative"],
            "neutral": scores["neutral"],
            "confidence": scores[best_label],
        }

    def analyze_batch(self, texts: list[str], batch_size: int = 16) -> list[dict]:
        """Score multiple texts at once in mini-batches.

        Processes texts in fixed-size batches to prevent OOM on large inputs.
        More efficient than calling analyze_text() in a loop because
        it batches the tokenization and inference.

        Args:
            texts: List of strings to analyze
            batch_size: Max texts per inference batch (default 16)

        Returns:
            List of score dicts (same format as analyze_text)
        """
        if not texts:
            return []

        self.load_model()

        all_results = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]

            tokens = self.tokenizer(
                batch,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            )

            with torch.no_grad():
                outputs = self.model(**tokens)
                probabilities = torch.softmax(outputs.logits, dim=1)

            for probs in probabilities:
                scores = {
                    label: prob.item()
                    for label, prob in zip(self.labels, probs)
                }
                best_label = max(scores, key=scores.get)
                all_results.append({
                    "label": best_label,
                    "positive": scores["positive"],
                    "negative": scores["negative"],
                    "neutral": scores["neutral"],
                    "confidence": scores[best_label],
                })

        return all_results

    def get_aggregate_sentiment(self, texts: list[str]) -> float:
        """Get a single sentiment score for a collection of texts.

        Analyzes all texts and returns a weighted average score
        from -1.0 (very negative) to +1.0 (very positive).

        The weighting uses confidence — high-confidence scores
        matter more than uncertain ones.

        Args:
            texts: List of strings (articles, posts, etc.)

        Returns:
            Float from -1.0 to +1.0
        """
        if not texts:
            return 0.0

        results = self.analyze_batch(texts)

        weighted_sum = 0.0
        total_weight = 0.0

        for result in results:
            # Convert to -1 to +1 scale
            score = result["positive"] - result["negative"]
            weight = result["confidence"]

            weighted_sum += score * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0

        aggregate = weighted_sum / total_weight

        logger.info(
            "Aggregate sentiment: %.3f from %d texts (avg confidence: %.2f)",
            aggregate, len(texts), total_weight / len(results)
        )

        return aggregate
