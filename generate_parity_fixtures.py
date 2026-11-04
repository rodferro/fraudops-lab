"""Generate Java parity references from the existing EXP-000 ONNX artifact.

Each case records two references:

* ``fraud_probability``: Python ONNX Runtime on the committed ``fraud_model.onnx``.
* ``sklearn_fraud_probability``: scikit-learn ``predict_proba`` of the EXP-000 pipeline.

No fitted scikit-learn pipeline is persisted, so the sklearn reference comes from an in-memory
refit of ``train.build_pipeline()`` on the committed CSV's chronological training split, exactly as
``train.py`` fits it. The refit is only used if it reproduces the committed ONNX model within the
parity tolerance; neither ``train.py`` nor the model artifacts are modified.
"""

from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path

import numpy as np
import onnxruntime as ort
import pandas as pd
import sklearn

import train

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "fraud_model.onnx"
METADATA_PATH = ROOT / "model_metadata.json"
DATA_PATH = ROOT / "data" / "synthetic_transactions.csv"
DESTINATION = ROOT / "src" / "test" / "resources" / "parity-cases.json"
ABSOLUTE_TOLERANCE = 1e-5
RELATIVE_TOLERANCE = 1e-4
# Train/validation boundaries used by train.train(); rows are already in timestamp order.
TRAIN_FRACTION = 0.70
VALIDATION_FRACTION = 0.85
SKLEARN_CHECK_ROWS = 512

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
    # Found by bisecting amount on the committed model: about 2e-4 either side of the
    # validation-selected threshold, roughly 12x the comparison tolerance.
    "just_below_threshold": {
        "amount": 474.74,
        "account_age_days": 400,
        "transactions_last_24h": 2,
        "distance_from_home_km": 20.0,
        "device_age_days": 200,
        "country_mismatch": 0,
        "card_present": 0,
        "channel_code": 0,
        "merchant_category_code": 2,
    },
    "just_above_threshold": {
        "amount": 476.74,
        "account_age_days": 400,
        "transactions_last_24h": 2,
        "distance_from_home_km": 20.0,
        "device_age_days": 200,
        "country_mismatch": 0,
        "card_present": 0,
        "channel_code": 0,
        "merchant_category_code": 2,
    },
}
EXPECTED_SIDE = {"just_below_threshold": "approve", "just_above_threshold": "review"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tolerance(expected: np.ndarray) -> np.ndarray:
    return ABSOLUTE_TOLERANCE + RELATIVE_TOLERANCE * np.abs(expected)


def onnx_scores(session: ort.InferenceSession, frame: pd.DataFrame, feature_order: list[str]) -> np.ndarray:
    inputs = {feature: frame[[feature]].to_numpy(dtype=np.float32) for feature in feature_order}
    return session.run(["probabilities"], inputs)[0][:, 1].astype(np.float64)


def cases_frame(feature_order: list[str]) -> pd.DataFrame:
    rows = [
        {feature: np.nan if features[feature] is None else float(features[feature]) for feature in feature_order}
        for features in CASES.values()
    ]
    return pd.DataFrame(rows, columns=feature_order)


def refit_sklearn_pipeline(session: ort.InferenceSession, feature_order: list[str]):
    """Refit the EXP-000 pipeline in memory and prove it is the committed ONNX model."""
    data = pd.read_csv(DATA_PATH)
    train_end = int(len(data) * TRAIN_FRACTION)
    validation_end = int(len(data) * VALIDATION_FRACTION)
    pipeline = train.build_pipeline()
    pipeline.fit(data.iloc[:train_end][train.FEATURES], data.iloc[:train_end]["is_fraud"].to_numpy())

    check = data.iloc[validation_end : validation_end + SKLEARN_CHECK_ROWS][feature_order]
    sklearn_scores = pipeline.predict_proba(check[train.FEATURES])[:, 1]
    committed_scores = onnx_scores(session, check, feature_order)
    difference = np.abs(sklearn_scores - committed_scores)
    if not np.all(difference <= tolerance(committed_scores)):
        raise SystemExit(
            "Refitted scikit-learn pipeline does not reproduce the committed ONNX model "
            f"(max abs diff {difference.max():.3g}); refusing to write sklearn references."
        )
    return pipeline, {
        "method": (
            "in-memory refit of train.build_pipeline() on the first 70% of data/synthetic_transactions.csv; "
            "the committed model is not modified"
        ),
        "data_sha256": sha256(DATA_PATH),
        "check_rows": int(len(check)),
        "check_rows_description": "first test-split rows, as in train.py's sklearn/ONNX parity check",
        "max_abs_diff_vs_committed_onnx": float(difference.max()),
    }


def main() -> None:
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    feature_order = metadata["feature_order"]
    if feature_order != train.FEATURES:
        raise SystemExit("model_metadata.json feature_order no longer matches train.FEATURES")
    threshold = metadata["evaluation_metrics"]["operating_point"]["probability_threshold"]
    session = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])

    pipeline, sklearn_reference = refit_sklearn_pipeline(session, feature_order)
    frame = cases_frame(feature_order)
    onnx_probabilities = onnx_scores(session, frame, feature_order)
    sklearn_probabilities = pipeline.predict_proba(frame[train.FEATURES])[:, 1]

    cases = []
    for (name, features), probability, sklearn_probability in zip(
        CASES.items(), onnx_probabilities, sklearn_probabilities
    ):
        decision = "review" if probability >= threshold else "approve"
        if name in EXPECTED_SIDE:
            margin = abs(probability - threshold)
            if decision != EXPECTED_SIDE[name] or margin <= 2 * tolerance(np.array([threshold]))[0]:
                raise SystemExit(f"{name} is not clearly on the {EXPECTED_SIDE[name]} side of the threshold")
        cases.append({
            "name": name,
            "features": features,
            "fraud_probability": float(probability),
            "sklearn_fraud_probability": float(sklearn_probability),
            "expected_decision": decision,
        })

    fixture = {
        "source": "Python ONNX Runtime and scikit-learn against the unchanged EXP-000 fraud_model.onnx",
        "model_version": metadata["model_version"],
        "model_sha256": sha256(MODEL_PATH),
        "metadata_sha256": sha256(METADATA_PATH),
        "review_threshold": threshold,
        "review_threshold_source": "model_metadata.json evaluation_metrics.operating_point.probability_threshold",
        "absolute_tolerance": ABSOLUTE_TOLERANCE,
        "relative_tolerance": RELATIVE_TOLERANCE,
        "generator": {
            "onnxruntime_version": ort.__version__,
            "scikit_learn_version": sklearn.__version__,
            "numpy_version": np.__version__,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "sklearn_reference": sklearn_reference,
        "cases": cases,
    }
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} references to {DESTINATION}")
    print(f"sklearn refit max abs diff vs committed ONNX: {sklearn_reference['max_abs_diff_vs_committed_onnx']:.3g}")


if __name__ == "__main__":
    main()
