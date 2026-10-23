"""Private synthetic cohorts and feedback; never imported by the inference API."""
import datetime as dt
import hashlib
import json

import numpy as np

FEATURES = ["amount", "account_age_days", "transactions_last_24h", "distance_from_home_km",
            "device_age_days", "country_mismatch", "card_present", "channel_code", "merchant_category_code"]
NUMERIC = FEATURES[:5]
CATEGORIES = {"country_mismatch": [0, 1], "card_present": [0, 1],
              "channel_code": [0, 1, 2], "merchant_category_code": [0, 1, 2, 3, 4]}
THRESHOLD = 0.06729906797409058
SCENARIOS = {
    "baseline": {"seed": 5001, "n": 5000, "rps": 100, "concurrency": 16},
    "burst": {"seed": 5002, "n": 5000, "rps": 500, "concurrency": 32},
    "recovery": {"seed": 5003, "n": 2000, "rps": 100, "concurrency": 16},
    "covariate": {"seed": 5004, "n": 5000, "rps": 100, "concurrency": 16},
    "concept": {"seed": 5005, "n": 5000, "rps": 100, "concurrency": 16},
    "deletion": {"seed": 5006, "n": 2000, "rps": 50, "concurrency": 16},
    "cpu_normal": {"seed": 5007, "n": 2000, "rps": 150, "concurrency": 16},
    "cpu_limited": {"seed": 5007, "n": 2000, "rps": 150, "concurrency": 16},
}
CORE = ["baseline", "burst", "covariate", "concept"]
CUTOFF_HOURS = [1, 12, 48, 168]


def iso(seconds):
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat()


def epoch(value):
    return dt.datetime.fromisoformat(value).timestamp()


def generate(name, n=None, seed=None):
    """Mirror EXP-000's distributions/log-odds before missingness, without training.

    Keep latent values until labels are sampled so covariate shifts retain the
    original conditional fraud mechanism. Changes to this recipe are explicit.
    """
    settings = SCENARIOS[name]
    n = settings["n"] if n is None else n
    seed = settings["seed"] if seed is None else seed
    rng = np.random.default_rng(seed)
    channel = rng.choice(3, n, p=[.15, .15, .70] if name == "covariate" else [.43, .36, .21])
    merchant = rng.choice(5, n, p=[.34, .18, .18, .12, .18])
    amount = rng.lognormal(3.9, .9, n) * (3 if name == "covariate" else 1)
    age = rng.integers(1, 4001, n)
    count = rng.poisson(2, n)
    distance = rng.exponential(15, n) * (6 if name == "covariate" else 1)
    device = rng.exponential(240, n) * (.15 if name == "covariate" else 1)
    country = rng.binomial(1, .035, n)
    card = ((channel == 1) & (rng.random(n) < .97)).astype(int)
    logit = (-7 + .4*np.log1p(amount) + .9*country + .72*(1-card) + .18*count
             + 1.1*(age < 30) + .35*(age < 180) + .4*(device < 30)
             + np.choose(merchant, [0, .05, .38, .62, .72]) + .1*(channel == 2))
    if name == "concept":
        # Reverse four relationships; fixed intercept offset, not fitted to API scores.
        logit += 4.2 - .8*np.log1p(amount) - 1.8*country - 1.44*(1-card) - .8*(device < 30)
    truth = rng.binomial(1, 1/(1+np.exp(-logit)))
    values = np.array([amount, age, count, distance, device, country, card, channel, merchant], dtype=float).T
    rates = [.005, .003, .01, .02, .02, .005, .005, .01, .01]
    if name == "covariate":
        rates[0], rates[3], rates[4] = .02, .08, .08
    for j, rate in enumerate(rates):
        values[rng.random(n) < rate, j] = np.nan
    # Simulated event time is independent of actual wall-clock request scheduling.
    day = list(SCENARIOS).index(name)
    start = epoch("2026-01-01T00:00:00+00:00") + day*86400
    return [{"transaction_id": f"exp005-{name}-{i:06d}", "scenario": name,
             "event_time": iso(start + 3600*i/n), "ground_truth": int(truth[i]),
             "features": {f: None if np.isnan(v) else float(v) for f, v in zip(FEATURES, row)}}
            for i, row in enumerate(values)]


def digest(rows):
    # Ignore run-dependent HTTP results; reproducible cohort fingerprint.
    fields = ["transaction_id", "scenario", "event_time", "ground_truth", "features"]
    return hashlib.sha256(json.dumps([{k: r[k] for k in fields} for r in rows],
                                     sort_keys=True, allow_nan=False).encode()).hexdigest()


def feedback(rows, seed, selected=False):
    """Feedback is generated once, then masked by arrival time at every cutoff."""
    rng = np.random.default_rng(seed + 100000)
    cohort_end = max(epoch(r["event_time"]) for r in rows)
    cutoffs = {str(h): cohort_end + h*3600 for h in CUTOFF_HOURS}
    for r in rows:
        fraud, review = bool(r["ground_truth"]), r.get("decision") == "review"
        observation_probability = (.95 if review else .15) if selected else .90
        observed = rng.random() < observation_probability
        median_hours = (48 if fraud else 6) * ((.25 if review else 3) if selected else 1)
        delay = rng.lognormal(np.log(median_hours), .8)*3600
        noisy = int(not fraud) if rng.random() < (.15 if fraud else .01) else int(fraud)
        r["feedback_arrival_time"] = iso(epoch(r["event_time"]) + delay) if observed else None
        r["feedback_label"] = noisy if observed else None
        r["feedback_policy"] = "review_selected" if selected else "random_missing"
        r["feedback_available"] = {h: bool(observed and epoch(r["feedback_arrival_time"]) <= t)
                                    for h, t in cutoffs.items()}
    return {h: iso(t) for h, t in cutoffs.items()}
