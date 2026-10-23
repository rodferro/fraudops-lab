"""Generate Java parity references from the existing EXP-000 ONNX artifact."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import onnxruntime as ort


ROOT = Path(__file__).resolve().parent
CASES = {
    "typical": {
        "amount": 250.0,
        "account_age_days": 120,
        "transactions_last_24h": 5,
        "distance_from_home_km": 150.0,
        "device_age_days": 30,
        "country_mismatch": 1,
        "card_present": 0,
        "channel_code": 0,
        "merchant_category_code": 2,
    },
    "high_risk": {
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
    "missing_numerical": {
        "amount": None,
        "account_age_days": 60,
        "transactions_last_24h": 1,
        "distance_from_home_km": 0.0,
        "device_age_days": None,
        "country_mismatch": 0,
        "card_present": 0,
        "channel_code": 1,
        "merchant_category_code": 0,
    },
    "unknown_categories": {
        "amount": 75.0,
        "account_age_days": 750,
        "transactions_last_24h": 3,
        "distance_from_home_km": 12.0,
        "device_age_days": 400,
        "country_mismatch": 0,
        "card_present": 1,
        "channel_code": 99,
        "merchant_category_code": 88,
    },
    "extreme_valid": {
        "amount": 1000000.0,
        "account_age_days": 4000,
        "transactions_last_24h": 100,
        "distance_from_home_km": 100000.0,
        "device_age_days": 10000,
        "country_mismatch": 1,
        "card_present": 0,
        "channel_code": 0,
        "merchant_category_code": 4,
    },
}


def main() -> None:
    metadata = json.loads((ROOT / "model_metadata.json").read_text(encoding="utf-8"))
    feature_order = metadata["feature_order"]
    session = ort.InferenceSession(str(ROOT / "fraud_model.onnx"), providers=["CPUExecutionProvider"])
    cases = []
    for name, features in CASES.items():
        inputs = {
            feature: np.asarray(
                [[np.nan if features[feature] is None else features[feature]]], dtype=np.float32
            )
            for feature in feature_order
        }
        outputs = session.run(["probabilities"], inputs)[0]
        cases.append({
            "name": name,
            "features": features,
            "fraud_probability": float(outputs[0, 1]),
        })

    fixture = {
        "source": "Python ONNX Runtime against the unchanged EXP-000 fraud_model.onnx",
        "model_version": metadata["model_version"],
        "absolute_tolerance": 1e-5,
        "relative_tolerance": 1e-4,
        "cases": cases,
    }
    destination = ROOT / "src" / "test" / "resources" / "parity-cases.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} references to {destination}")


if __name__ == "__main__":
    main()