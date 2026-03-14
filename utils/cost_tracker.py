import json
import logging
import os
from datetime import datetime


class CostTracker:
    def __init__(self, log_path=None):
        if log_path is None:
            # Use /tmp in Lambda, otherwise local .tmp
            is_lambda = os.environ.get("AWS_LAMBDA_FUNCTION_NAME") is not None
            self.log_path = "/tmp/cost_tracking.json" if is_lambda else ".tmp/cost_tracking.json"
        else:
            self.log_path = log_path
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.log_path):
            try:
                with open(self.log_path, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0.0,
            "runs": []
        }

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, 'w') as f:
                json.dump(self.data, f, indent=2)
        except OSError as e:
            logging.warning(f"Failed to save cost tracking to {self.log_path}: {e}")

    def log_usage(self, model_name, input_tokens, output_tokens):
        # Vertex AI Gemini 1.5 Flash Pricing (approx)
        # Input: $0.075 / 1M tokens
        # Output: $0.30 / 1M tokens
        
        input_cost = (input_tokens / 1_000_000) * 0.075
        output_cost = (output_tokens / 1_000_000) * 0.30
        total_item_cost = input_cost + output_cost

        self.data["total_input_tokens"] += input_tokens
        self.data["total_output_tokens"] += output_tokens
        self.data["total_cost_usd"] += total_item_cost
        
        # Add to current run summary
        logging.info(f"Cost Tracker: {model_name} | In: {input_tokens} | Out: {output_tokens} | Cost: ${total_item_cost:.6f}")
        self._save()

    def get_summary(self):
        return {
            "total_cost": f"${self.data['total_cost_usd']:.4f}",
            "tokens": f"{self.data['total_input_tokens']} in / {self.data['total_output_tokens']} out"
        }

tracker = CostTracker()
