"""Smoke-test ONNX inference without importing scikit-learn."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort


def load_rows(
    csv_path: Path | None, feature_order: list[str], max_rows: int
) -> dict[str, np.ndarray]:
    if csv_path is None:
        examples = [
            {
                "amount": 42.50,
                "account_age_days": 850,
                "transactions_last_24h": 2,
                "distance_from_home_km": 4.0,
                "device_age_days": 120,
                "country_mismatch": 0,
                "card_present": 1,
                "channel_code": 1,
                "merchant_category_code": 0,
            },
            {
                "amount": 1800.0,
                "account_age_days": 9,
                "transactions_last_24h": 7,
                "distance_from_home_km": 250.0,
                "device_age_days": 2,
                "country_mismatch": 1,
                "card_present": 0,
                "channel_code": 2,
                "merchant_category_code": 4,
            },
            {
                "amount": float("nan"),
                "account_age_days": 60,
                "transactions_last_24h": 1,
                "distance_from_home_km": 0.0,
                "device_age_days": float("nan"),
                "country_mismatch": 0,
                "card_present": 0,
                "channel_code": float("nan"),
                "merchant_category_code": 99,
            },
        ]
    else:
        with csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing_columns = set(feature_order) - set(reader.fieldnames or [])
            if missing_columns:
                raise ValueError(f"CSV is missing input columns: {sorted(missing_columns)}")
            examples = []
            for row in reader:
                examples.append(
                    {
                        feature: float(row[feature]) if row[feature].strip() else float("nan")
                        for feature in feature_order
                    }
                )
                if len(examples) >= max_rows:
                    break
    if not examples:
        raise ValueError("The input CSV contains no transaction rows")
    return {
        feature: np.asarray([[row[feature]] for row in examples], dtype=np.float32)
        for feature in feature_order
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=Path(__file__).with_name("fraud_model.onnx"))
    parser.add_argument("--metadata", type=Path, default=Path(__file__).with_name("model_metadata.json"))
    parser.add_argument("--input-csv", type=Path, help="Optional CSV with one row per transaction")
    parser.add_argument("--max-rows", type=int, default=100, help="Maximum CSV rows to smoke-test")
    parser.add_argument("--threshold", type=float, help="Optional decision threshold override")
    args = parser.parse_args()
    if args.max_rows < 1:
        parser.error("--max-rows must be positive")

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    feature_order = metadata["feature_order"]
    session = ort.InferenceSession(str(args.model), providers=["CPUExecutionProvider"])
    inputs = load_rows(args.input_csv, feature_order, args.max_rows)
    input_names = {item.name for item in session.get_inputs()}
    if set(inputs) != input_names:
        raise ValueError(f"Input schema mismatch: expected {sorted(input_names)}, got {sorted(inputs)}")

    outputs = session.run(None, inputs)
    probability_tensor = next(
        (
            value
            for value in outputs
            if isinstance(value, np.ndarray) and value.ndim == 2 and value.shape[1] == 2
        ),
        None,
    )
    if probability_tensor is None:
        raise RuntimeError("ONNX model did not return a two-class probability tensor")
    probabilities = probability_tensor[:, 1]
    if not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any():
        raise AssertionError("Model returned an invalid fraud probability")

    threshold = args.threshold
    if threshold is None:
        threshold = metadata["evaluation_metrics"]["operating_point"]["probability_threshold"]
    result = {
        "model_version": metadata["model_version"],
        "rows_scored": int(len(probabilities)),
        "threshold": threshold,
        "predictions": [
            {
                "fraud_probability": float(probability),
                "decision": "review" if probability >= threshold else "no_review",
            }
            for probability in probabilities
        ],
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()