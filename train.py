"""Generate a reproducible synthetic dataset, train, evaluate, and export fraud scoring."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import onnx
import onnxruntime as ort
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType


SEED = 20261023
ROW_COUNT = 50_000
MODEL_VERSION = "1.0.0-exp000"
NUMERIC_FEATURES = [
    "amount",
    "account_age_days",
    "transactions_last_24h",
    "distance_from_home_km",
    "device_age_days",
    "country_mismatch",
    "card_present",
]
CATEGORICAL_FEATURES = ["channel_code", "merchant_category_code"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
CATEGORY_CODES = {
    "channel_code": {"0": "ecommerce", "1": "in_store", "2": "mobile"},
    "merchant_category_code": {
        "0": "grocery",
        "1": "fuel",
        "2": "electronics",
        "3": "travel",
        "4": "digital_goods",
    },
}
CATEGORY_VALUES = [
    np.array([-1.0, 0.0, 1.0, 2.0], dtype=np.float32),
    np.array([-1.0, 0.0, 1.0, 2.0, 3.0, 4.0], dtype=np.float32),
]
FEATURE_DESCRIPTIONS = {
    "amount": ("Transaction amount", "USD; finite nonnegative value"),
    "account_age_days": ("Age of the account", "days; nonnegative value"),
    "transactions_last_24h": ("Recent transaction count", "count; nonnegative integer"),
    "distance_from_home_km": ("Transaction distance from home", "kilometers; nonnegative value"),
    "device_age_days": ("Age of the device", "days; nonnegative value"),
    "country_mismatch": ("Whether transaction country differs from account country", "0 or 1"),
    "card_present": ("Whether the physical card was present", "0 or 1"),
    "channel_code": ("Transaction channel code", "integer category code"),
    "merchant_category_code": ("Merchant category code", "integer category code"),
}
REJECTION_RATES = (0.005, 0.01, 0.02, 0.05)


def generate_dataset(row_count: int = ROW_COUNT, seed: int = SEED) -> pd.DataFrame:
    """Create transactions with a known, deliberately imperfect fraud signal."""
    rng = np.random.default_rng(seed)
    channel = rng.choice(3, size=row_count, p=[0.43, 0.36, 0.21])
    merchant = rng.choice(5, size=row_count, p=[0.34, 0.18, 0.18, 0.12, 0.18])
    amount = rng.lognormal(mean=3.9, sigma=0.9, size=row_count)
    account_age = rng.integers(1, 4001, size=row_count)
    transactions = rng.poisson(2.0, size=row_count)
    distance = rng.exponential(15.0, size=row_count)
    device_age = rng.exponential(240.0, size=row_count)
    country_mismatch = rng.binomial(1, 0.035, size=row_count)
    card_present = ((channel == 1) & (rng.random(row_count) < 0.97)).astype(int)

    merchant_effect = np.choose(merchant, [0.0, 0.05, 0.38, 0.62, 0.72])
    log_odds = (
        -7.0
        + 0.40 * np.log1p(amount)
        + 0.90 * country_mismatch
        + 0.72 * (1 - card_present)
        + 0.18 * transactions
        + 1.10 * (account_age < 30)
        + 0.35 * (account_age < 180)
        + 0.40 * (device_age < 30)
        + merchant_effect
        + 0.10 * (channel == 2)
    )
    fraud_probability = 1.0 / (1.0 + np.exp(-log_odds))
    fraud = rng.binomial(1, fraud_probability)

    start = pd.Timestamp("2025-01-01", tz="UTC")
    seconds = rng.integers(0, 365 * 24 * 60 * 60, size=row_count)
    timestamps = start + pd.to_timedelta(seconds, unit="s")
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "amount": amount,
            "account_age_days": account_age,
            "transactions_last_24h": transactions,
            "distance_from_home_km": distance,
            "device_age_days": device_age,
            "country_mismatch": country_mismatch,
            "card_present": card_present,
            "channel_code": channel,
            "merchant_category_code": merchant,
            "is_fraud": fraud,
        }
    )

    missing_rates = {
        "amount": 0.005,
        "account_age_days": 0.003,
        "transactions_last_24h": 0.01,
        "distance_from_home_km": 0.02,
        "device_age_days": 0.02,
        "country_mismatch": 0.005,
        "card_present": 0.005,
        "channel_code": 0.01,
        "merchant_category_code": 0.01,
    }
    for feature, rate in missing_rates.items():
        frame.loc[rng.random(row_count) < rate, feature] = np.nan
    return frame.sort_values("timestamp", kind="stable").reset_index(drop=True)


def build_pipeline() -> Pipeline:
    numeric_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )
    categorical_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="constant", fill_value=-1.0)),
            (
                "encoder",
                OneHotEncoder(
                    categories=CATEGORY_VALUES,
                    handle_unknown="ignore",
                    sparse_output=False,
                ),
            ),
        ]
    )
    preprocessor = ColumnTransformer(
        [
            ("numeric", numeric_pipeline, NUMERIC_FEATURES),
            ("categorical", categorical_pipeline, CATEGORICAL_FEATURES),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )
    return Pipeline(
        [
            ("preprocessor", preprocessor),
            (
                "classifier",
                LogisticRegression(
                    C=1.0,
                    class_weight=None,
                    max_iter=1000,
                    random_state=SEED,
                    solver="lbfgs",
                ),
            ),
        ]
    )


def make_onnx_inputs(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    return {
        feature: frame[[feature]].to_numpy(dtype=np.float32)
        for feature in FEATURES
    }


def onnx_probability(session: ort.InferenceSession, frame: pd.DataFrame) -> np.ndarray:
    outputs = session.run(None, make_onnx_inputs(frame))
    for output in outputs:
        if isinstance(output, np.ndarray) and output.ndim == 2 and output.shape[1] == 2:
            return output[:, 1].astype(np.float64)
    raise RuntimeError("Could not locate the ONNX probability tensor with two classes")


def choose_threshold(scores: np.ndarray, target_rejection_rate: float) -> float:
    count = max(1, int(np.ceil(target_rejection_rate * len(scores))))
    ordered = np.sort(scores)
    lower = ordered[-count - 1] if count < len(ordered) else 0.0
    upper = ordered[-count]
    if upper > lower:
        return float((lower + upper) / 2.0)
    return float(upper)


def fixed_rejection_metrics(
    labels: np.ndarray, scores: np.ndarray
) -> dict[str, dict[str, float]]:
    ranking = np.argsort(-scores, kind="stable")
    result: dict[str, dict[str, float]] = {}
    for rate in REJECTION_RATES:
        count = max(1, int(np.ceil(rate * len(labels))))
        selected = np.zeros(len(labels), dtype=bool)
        selected[ranking[:count]] = True
        result[f"{rate:.1%}"] = {
            "rejection_rate": float(selected.mean()),
            "precision": float(precision_score(labels, selected, zero_division=0)),
            "recall": float(recall_score(labels, selected, zero_division=0)),
        }
    return result


def evaluate_split(labels: np.ndarray, scores: np.ndarray) -> dict[str, Any]:
    quantiles = np.quantile(scores, [0.0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0])
    return {
        "rows": int(len(labels)),
        "fraud_count": int(labels.sum()),
        "fraud_prevalence": float(labels.mean()),
        "roc_auc": float(roc_auc_score(labels, scores)),
        "average_precision": float(average_precision_score(labels, scores)),
        "precision_recall_at_rejection_rates": fixed_rejection_metrics(labels, scores),
        "score_quantiles": {
            key: float(value)
            for key, value in zip(
                ["min", "p25", "p50", "p75", "p90", "p95", "p99", "max"],
                quantiles,
            )
        },
    }


def measure_latency(session: ort.InferenceSession, sample: pd.DataFrame) -> dict[str, float]:
    inputs = make_onnx_inputs(sample.iloc[[0]])
    for _ in range(100):
        session.run(None, inputs)
    elapsed = np.empty(2000, dtype=np.float64)
    for index in range(len(elapsed)):
        started = time.perf_counter_ns()
        session.run(None, inputs)
        elapsed[index] = (time.perf_counter_ns() - started) / 1000.0
    return {
        "measurement": "single-row ONNX Runtime CPU session.run; warmed; excludes transport",
        "samples": int(len(elapsed)),
        "p50_microseconds": float(np.percentile(elapsed, 50)),
        "p95_microseconds": float(np.percentile(elapsed, 95)),
    }


def json_dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def train(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "data").mkdir(parents=True, exist_ok=True)
    data = generate_dataset()
    data.to_csv(output_dir / "data" / "synthetic_transactions.csv", index=False)

    train_end = int(len(data) * 0.70)
    validation_end = int(len(data) * 0.85)
    train_data = data.iloc[:train_end]
    validation_data = data.iloc[train_end:validation_end]
    test_data = data.iloc[validation_end:]
    pipeline = build_pipeline()
    pipeline.fit(train_data[FEATURES], train_data["is_fraud"].to_numpy())

    initial_types = [(feature, FloatTensorType([None, 1])) for feature in FEATURES]
    onnx_model = convert_sklearn(
        pipeline,
        initial_types=initial_types,
        options={id(pipeline.named_steps["classifier"]): {"zipmap": False}},
        target_opset=17,
    )
    onnx_path = output_dir / "fraud_model.onnx"
    onnx.save_model(onnx_model, onnx_path)
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])

    validation_scores = onnx_probability(session, validation_data[FEATURES])
    threshold = choose_threshold(validation_scores, 0.01)
    splits = {"train": train_data, "validation": validation_data, "test": test_data}
    split_scores = {
        name: onnx_probability(session, subset[FEATURES])
        for name, subset in splits.items()
    }
    metrics: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "random_seed": SEED,
        "split_method": "chronological 70% / 15% / 15% by timestamp; no shuffling",
        "class_imbalance": {
            name: {
                "fraud_count": int(subset["is_fraud"].sum()),
                "rows": int(len(subset)),
                "fraud_prevalence": float(subset["is_fraud"].mean()),
            }
            for name, subset in splits.items()
        },
        "operating_point": {
            "selection": "validation scores; target 1% rejection rate",
            "probability_threshold": threshold,
            "validation_rejection_rate": float(np.mean(validation_scores >= threshold)),
            "validation_precision": float(
                precision_score(
                    validation_data["is_fraud"],
                    validation_scores >= threshold,
                    zero_division=0,
                )
            ),
            "validation_recall": float(
                recall_score(
                    validation_data["is_fraud"],
                    validation_scores >= threshold,
                    zero_division=0,
                )
            ),
        },
        "splits": {
            name: evaluate_split(subset["is_fraud"].to_numpy(), split_scores[name])
            for name, subset in splits.items()
        },
    }
    test_at_validation_threshold = split_scores["test"] >= threshold
    metrics["test_at_validation_threshold"] = {
        "probability_threshold_selected_on_validation": threshold,
        "test_rejection_rate": float(test_at_validation_threshold.mean()),
        "test_precision": float(
            precision_score(
                test_data["is_fraud"], test_at_validation_threshold, zero_division=0
            )
        ),
        "test_recall": float(
            recall_score(
                test_data["is_fraud"], test_at_validation_threshold, zero_division=0
            )
        ),
    }

    parity_data = test_data.iloc[: min(512, len(test_data))][FEATURES]
    sklearn_scores = pipeline.predict_proba(parity_data)[:, 1]
    parity_scores = onnx_probability(session, parity_data)
    atol = 1e-5
    rtol = 1e-4
    np.testing.assert_allclose(parity_scores, sklearn_scores, atol=atol, rtol=rtol)
    metrics["onnx_parity"] = {
        "samples": int(len(parity_data)),
        "absolute_tolerance": atol,
        "relative_tolerance": rtol,
        "maximum_absolute_difference": float(np.max(np.abs(parity_scores - sklearn_scores))),
        "passed": True,
    }
    metrics["prediction_latency"] = measure_latency(session, test_data[FEATURES])
    json_dump(output_dir / "metrics.json", metrics)

    schema = {
        feature: {
            "description": FEATURE_DESCRIPTIONS[feature][0],
            "tensor_type": "float32",
            "shape": ["batch", 1],
            "nullable": True,
            "unit_or_values": FEATURE_DESCRIPTIONS[feature][1],
            "missing_behavior": (
                "imputed with training median before standardization"
                if feature in NUMERIC_FEATURES
                else "imputed to -1 and encoded as the reserved missing category"
            ),
            **(
                {"category_codes": CATEGORY_CODES[feature]}
                if feature in CATEGORICAL_FEATURES
                else {}
            ),
        }
        for feature in FEATURES
    }
    metadata = {
        "model_version": MODEL_VERSION,
        "model_type": "scikit-learn LogisticRegression; exported to ONNX",
        "input_schema": schema,
        "feature_order": FEATURES,
        "output_schema": {
            "fraud_probability": {
                "type": "float32",
                "range": [0.0, 1.0],
                "meaning": "estimated probability of the positive is_fraud class",
            },
            "onnx_probability_tensor": {
                "name": "probabilities",
                "type": "float32",
                "shape": ["batch", 2],
                "class_order": [0, 1],
                "fraud_probability_column": 1,
            },
            "onnx_label_tensor": {
                "name": "label",
                "type": "int64",
                "shape": ["batch"],
                "note": "Classifier output only; use the probability and service-configured policy for decisions.",
            },
            "model_version": MODEL_VERSION,
            "decision": "optional; fraud_probability >= configured threshold",
        },
        "preprocessing": {
            "numeric": "median imputation followed by StandardScaler",
            "categorical": "missing sentinel -1 followed by OneHotEncoder; unknown codes ignored",
            "classifier": "C=1.0, lbfgs, max_iter=1000, no class weighting",
            "class_imbalance_handling": "measured; no resampling or class weighting to preserve probability semantics",
            "timestamp": "used only for chronological splitting; excluded from model inputs",
        },
        "training_data_provenance": {
            "source": "deterministically generated synthetic dataset",
            "file": "data/synthetic_transactions.csv",
            "rows": int(len(data)),
            "random_seed": SEED,
            "label_assumptions": "sampled from a seeded logistic risk function of transaction features",
            "limitations": "not real-world data; feature distributions and fraud mechanisms are illustrative",
        },
        "evaluation_metrics": metrics,
        "integration_contract": {
            "input": "one request or batch of float32 values in feature_order; null becomes NaN",
            "output": "fraud_probability and model_version; decision is optional and threshold-configurable",
            "risk_decision_separation": "return the risk score independently; do not hard-code approve/reject into the model",
            "runtime": "ONNX Runtime CPU; scikit-learn is not required for inference",
        },
    }
    json_dump(output_dir / "model_metadata.json", metadata)

    print(f"Saved {onnx_path}")
    print(f"Rows: {len(data)}; train/validation/test: {train_end}/{validation_end - train_end}/{len(data) - validation_end}")
    print(f"Test ROC-AUC: {metrics['splits']['test']['roc_auc']:.4f}")
    print(f"Test average precision: {metrics['splits']['test']['average_precision']:.4f}")
    print(f"Parity max abs diff: {metrics['onnx_parity']['maximum_absolute_difference']:.3g}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "artifacts" / "EXP-000",
        help="Directory for generated dataset/model artifacts (default: artifacts/EXP-000)",
    )
    args = parser.parse_args()
    train(args.output_dir.resolve())


if __name__ == "__main__":
    main()
