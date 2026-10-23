# EXP-000 Fraud Detection Model Report

## Outcome

EXP-000 produces a reproducible, low-latency logistic-regression baseline, exports the complete scikit-learn preprocessing and classifier pipeline to ONNX, and verifies Python/ONNX parity. The artifact is technically ready for Java integration. It is **not production-approved for fraud decisions**: the generated data is synthetic and held-out precision/recall are modest.

## Dataset and Splits

`train.py` generates 50,000 synthetic transactions with a fixed seed (`20261023`) and UTC timestamps across 2025. Numeric fields cover amount, account age, recent transaction count, distance from home, device age, country mismatch, and card presence. Channel and merchant category are categorical features represented by documented numeric codes. Missing values are injected before training. Labels are sampled from an illustrative logistic risk function of these features; the timestamp is used only to order rows and is excluded from model inputs.

This is a synthetic dataset, not an external benchmark or representative customer sample. Its feature distributions and label-generation rule are known by construction, so reported scores demonstrate a working pipeline rather than expected real-world performance. The generated CSV is written to `data/synthetic_transactions.csv`.

Rows are sorted by timestamp and split without shuffling: 35,000 train (70%), 7,500 validation (15%), and 7,500 test (15%). Fraud prevalence is 1.77% in train, 1.59% in validation, and 1.76% in test. No resampling or class weighting is used; the imbalance is measured directly and the unweighted logistic model retains probability semantics. The validation set alone selects the optional operating threshold. Test data is used only for final evaluation and parity verification.

## Model and Evaluation

The pipeline median-imputes and standard-scales numeric fields; categorical code fields are imputed to reserved code `-1` and one-hot encoded. Unknown category codes are ignored by the encoder. A regularized scikit-learn `LogisticRegression` (`C=1`, `lbfgs`) is exported together with preprocessing.

| Test metric | Result |
| --- | ---: |
| Rows / frauds | 7,500 / 132 |
| Fraud prevalence | 1.76% |
| ROC-AUC | 0.6959 |
| PR-AUC (Average Precision) | 0.03831 |

Precision and recall at fixed rejection rates are ranking diagnostics: each row selects the exact top `ceil(rate * rows)` test scores, with stable input order for score ties. These test-set rates do not set the deployed threshold.

| Test rejection rate | Precision | Recall |
| ---: | ---: | ---: |
| 0.5% | 7.89% | 2.27% |
| 1% | 6.67% | 3.79% |
| 2% | 7.33% | 8.33% |
| 5% | 4.80% | 13.64% |

The validation-selected threshold is `0.06729906797409058`, targeting a 1% rejection rate. On validation it rejects 1.00% of rows (6.67% precision, 4.20% recall). Applied unchanged to the test set, it rejects 0.91% (5.88% precision, 3.03% recall). The score remains separate from the service's configurable review/approve/decline policy.

Test score quantiles: min `0.00363`, p25 `0.01010`, p50 `0.01466`, p75 `0.02148`, p90 `0.03075`, p95 `0.03923`, p99 `0.06411`, max `0.41569`.

Warm single-row ONNX Runtime CPU latency measured over 2,000 calls was 29.2 microseconds p50 and 45.5 microseconds p95 on the local development environment. This measures `session.run` only, excluding Java request parsing, feature mapping, and service/network overhead; it is not a deployment hardware benchmark and varies with host load.

## ONNX Parity

The training run compares 512 held-out test rows from scikit-learn and ONNX Runtime. `numpy.testing.assert_allclose` uses absolute tolerance `1e-5` and relative tolerance `1e-4`. The check passed; maximum absolute probability difference was `8.46e-08`. No discrepancies outside tolerance were observed.

## Java Inference Contract

- Load `fraud_model.onnx` using ONNX Runtime. The model uses ONNX opset 17.
- Supply all nine inputs by their names in `model_metadata.json` as `float32` tensors shaped `[batch, 1]`. Feature order is also recorded there. Category code mappings are `channel_code`: `0=ecommerce`, `1=in_store`, `2=mobile`; and `merchant_category_code`: `0=grocery`, `1=fuel`, `2=electronics`, `3=travel`, `4=digital_goods`.
- Numeric missing values are median-imputed inside the model. For missing inputs, Java should translate API `null` to IEEE `NaN` in the tensor. Missing category codes are imputed to the internal reserved category `-1`. Unknown non-missing category codes are accepted and encoded as all-zero for that field.
- Read `probabilities` (`float32`, `[batch, 2]`); column 1 is the fraud probability. `label` (`int64`, `[batch]`) is the classifier's ancillary class output and should not replace service policy.
- Return the probability in `[0,1]` and model version `1.0.0-exp000`. If desired, compute a separate decision such as `review` when probability is at or above a service-configured threshold. The validation-selected threshold is a starting reference, not a universal policy.
- Inference requires ONNX Runtime and numeric tensor handling; scikit-learn is only needed for training and parity validation.

## Reproduction

```powershell
python -m pip install -r requirements.txt
python train.py --output-dir artifacts/EXP-000/reproduction
python verify_onnx.py --model artifacts/EXP-000/reproduction/fraud_model.onnx --metadata artifacts/EXP-000/reproduction/model_metadata.json
python verify_onnx.py --model artifacts/EXP-000/reproduction/fraud_model.onnx --metadata artifacts/EXP-000/reproduction/model_metadata.json --input-csv artifacts/EXP-000/reproduction/data/synthetic_transactions.csv
```

`train.py` regenerates the CSV, model, metadata, metrics, and parity result. `verify_onnx.py` is a standalone ONNX Runtime inference smoke test and does not import scikit-learn. Exact dependency versions are pinned in `requirements.txt`.

After repository organization, generated training outputs default to ignored `artifacts/EXP-000/`; the commands above use a separate reproduction directory. The original metrics snapshot is retained in [evidence/metrics.json](evidence/metrics.json). The accepted root-level model/metadata and `data/synthetic_transactions.csv` remain in place; the metadata references that CSV. Running `python verify_onnx.py` without path overrides still checks the accepted root-level model.

## Limitations and Readiness

The positive class is rare, and the test average precision is only about 2.2 times test prevalence; at the validation-selected operating point, test recall is 3.03%. Synthetic labels, simplified features, no external validation, and no production hardware benchmark prevent any claim of operational fraud effectiveness. Before real use, retrain and evaluate on approved historical data, assess calibration and temporal drift, review feature availability and privacy, and set decision policy with business costs and human review in mind.

The ONNX artifact, explicit tensor contract, metadata, and no-scikit-learn inference path are ready for integration testing in the future Java scoring service. This report does not begin EXP-001.
