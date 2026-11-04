package com.fraudops.service;

import com.fraudops.api.PredictionRequest;
import com.fraudops.api.PredictionResponse;
import com.fraudops.inference.FraudModel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
public class PredictionService {
    /**
     * 2^24: every integer up to this magnitude is exactly representable as float32, so integer
     * features cannot be silently rounded on their way into the tensor. It is far above any
     * plausible amount, day count, transaction count, distance or category code.
     */
    static final double MAX_EXACT_FLOAT32_INTEGER = 16_777_216.0;

    static final String METADATA_SOURCE = "model_metadata.json evaluation_metrics.operating_point.probability_threshold";
    static final String OVERRIDE_SOURCE = "fraud.decision.review-threshold (FRAUD_REVIEW_THRESHOLD)";
    private static final Logger log = LoggerFactory.getLogger(PredictionService.class);

    private final FraudModel fraudModel;
    private final double reviewThreshold;
    private final String reviewThresholdSource;

    /**
     * The model metadata's validation-selected threshold is the default; a non-blank
     * {@code fraud.decision.review-threshold} (env {@code FRAUD_REVIEW_THRESHOLD}) overrides it.
     * Startup fails if neither provides a probability.
     */
    public PredictionService(
            FraudModel fraudModel,
            @Value("${fraud.decision.review-threshold:}") String configuredThreshold) {
        this.fraudModel = fraudModel;
        if (configuredThreshold != null && !configuredThreshold.isBlank()) {
            try {
                this.reviewThreshold = Double.parseDouble(configuredThreshold.strip());
            } catch (NumberFormatException exception) {
                throw new IllegalArgumentException("Fraud review threshold override must be a number.", exception);
            }
            this.reviewThresholdSource = OVERRIDE_SOURCE;
        } else {
            this.reviewThreshold = fraudModel.metadataReviewThreshold().orElseThrow(() -> new IllegalStateException(
                    "No fraud review threshold: model metadata has none and no override is configured."));
            this.reviewThresholdSource = METADATA_SOURCE;
        }
        if (!Double.isFinite(reviewThreshold) || reviewThreshold < 0.0 || reviewThreshold > 1.0) {
            throw new IllegalArgumentException("Fraud review threshold must be finite and between 0 and 1.");
        }
        log.info("event=review_threshold_configured threshold={} source=\"{}\" model_version={}",
                reviewThreshold, reviewThresholdSource, fraudModel.modelVersion());
        if (OVERRIDE_SOURCE.equals(reviewThresholdSource)) {
            // A pinned override silently outlives a retrain; make a disagreement visible.
            fraudModel.metadataReviewThreshold()
                    .ifPresent(metadataThreshold -> warnIfOverrideDiffers(metadataThreshold, fraudModel.modelVersion()));
        }
    }

    private void warnIfOverrideDiffers(double metadataThreshold, String modelVersion) {
        if (Double.compare(reviewThreshold, metadataThreshold) != 0) {
            log.warn("event=review_threshold_override_differs override={} metadata={} model_version={}",
                    reviewThreshold, metadataThreshold, modelVersion);
        }
    }

    public double reviewThreshold() {
        return reviewThreshold;
    }

    public String reviewThresholdSource() {
        return reviewThresholdSource;
    }

    public PredictionResponse predict(PredictionRequest request) {
        if (request == null) {
            throw new FeatureValidationException(List.of("request body is required"));
        }
        Map<String, Float> features = mapAndValidate(request);
        double probability = fraudModel.predictProbability(features);
        return new PredictionResponse(probability, fraudModel.modelVersion(),
                probability >= reviewThreshold ? "review" : "approve");
    }

    private Map<String, Float> mapAndValidate(PredictionRequest request) {
        LinkedHashMap<String, Double> values = new LinkedHashMap<>();
        values.put("amount", request.amount());
        values.put("account_age_days", request.account_age_days());
        values.put("transactions_last_24h", request.transactions_last_24h());
        values.put("distance_from_home_km", request.distance_from_home_km());
        values.put("device_age_days", request.device_age_days());
        values.put("country_mismatch", request.country_mismatch());
        values.put("card_present", request.card_present());
        values.put("channel_code", request.channel_code());
        values.put("merchant_category_code", request.merchant_category_code());

        List<String> errors = new ArrayList<>();
        LinkedHashMap<String, Float> mapped = new LinkedHashMap<>();
        values.forEach((name, value) -> {
            if (value == null) {
                mapped.put(name, Float.NaN);
                return;
            }
            if (!Double.isFinite(value) || !Float.isFinite(value.floatValue())) {
                errors.add(name + " must be a finite float32 value or null");
                return;
            }
            if (isNonNegativeFeature(name) && value < 0.0) {
                errors.add(name + " must be nonnegative");
            }
            if (value > MAX_EXACT_FLOAT32_INTEGER) {
                errors.add(name + " must be at most " + (long) MAX_EXACT_FLOAT32_INTEGER);
            }
            if (isIntegerFeature(name) && value != Math.rint(value)) {
                errors.add(name + " must be an integer-valued number");
            }
            if (isBinaryFeature(name) && value != 0.0 && value != 1.0) {
                errors.add(name + " must be 0 or 1");
            }
            mapped.put(name, value.floatValue());
        });
        if (!errors.isEmpty()) {
            throw new FeatureValidationException(errors);
        }
        return Map.copyOf(mapped);
    }

    private boolean isNonNegativeFeature(String name) {
        return switch (name) {
            // Category codes too: -1 is the model's internal missing-category sentinel, so a client
            // must send null rather than -1 (or any other negative code).
            case "amount", "account_age_days", "transactions_last_24h", "distance_from_home_km", "device_age_days",
                    "channel_code", "merchant_category_code" -> true;
            default -> false;
        };
    }

    private boolean isIntegerFeature(String name) {
        return switch (name) {
            case "account_age_days", "transactions_last_24h", "country_mismatch", "card_present",
                    "channel_code", "merchant_category_code" -> true;
            default -> false;
        };
    }

    private boolean isBinaryFeature(String name) {
        return name.equals("country_mismatch") || name.equals("card_present");
    }
}