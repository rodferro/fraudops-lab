package com.fraudops.service;

import com.fraudops.api.PredictionRequest;
import com.fraudops.api.PredictionResponse;
import com.fraudops.inference.FraudModel;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PredictionServiceTest {
    private AtomicReference<Map<String, Float>> capturedFeatures;
    private double modelProbability;
    private PredictionService service;

    @BeforeEach
    void setUp() {
        capturedFeatures = new AtomicReference<>();
        modelProbability = 0.06729906797409058;
        FraudModel model = new FraudModel() {
            @Override
            public double predictProbability(Map<String, Float> features) {
                capturedFeatures.set(features);
                return modelProbability;
            }

            @Override
            public String modelVersion() {
                return "1.0.0-exp000";
            }

            @Override
            public boolean isReady() {
                return true;
            }
        };
        service = new PredictionService(model, 0.06729906797409058);
    }

    @Test
    void mapsNamedFeaturesAndPreservesMissingAsNan() {
        PredictionRequest request = new PredictionRequest(250.0, 120.0, null, 150.0, 30.0,
                1.0, 0.0, 99.0, 88.0);

        service.predict(request);

        Map<String, Float> mapped = capturedFeatures.get();
        assertEquals(250.0f, mapped.get("amount"));
        assertTrue(Float.isNaN(mapped.get("transactions_last_24h")));
        assertEquals(99.0f, mapped.get("channel_code"));
    }

    @Test
    void thresholdBoundaryIsReviewAndVersionIsPropagated() {
        PredictionResponse response = service.predict(validRequest());

        assertEquals("review", response.decision());
        assertEquals("1.0.0-exp000", response.model_version());
        assertEquals(modelProbability, response.fraud_probability());
    }

    @Test
    void probabilityBelowThresholdIsApprove() {
        modelProbability = 0.067299;

        assertEquals("approve", service.predict(validRequest()).decision());
    }

    @Test
    void rejectsInvalidNumericalAndBinaryValuesButAllowsUnknownCategories() {
        PredictionRequest invalid = new PredictionRequest(1.0, 12.5, 1.0, 0.0, 5.0,
                2.0, 0.0, 1.0, 0.0);

        FeatureValidationException exception = assertThrows(
                FeatureValidationException.class, () -> service.predict(invalid));

        assertFalse(exception.getDetails().isEmpty());
    }

    @Test
    void rejectsInfiniteNumberWithoutCoercingIt() {
        PredictionRequest invalid = new PredictionRequest(Double.POSITIVE_INFINITY, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0, 0.0, 0.0);

        assertThrows(FeatureValidationException.class, () -> service.predict(invalid));
    }

    private PredictionRequest validRequest() {
        return new PredictionRequest(250.0, 120.0, 5.0, 150.0, 30.0,
                1.0, 0.0, 0.0, 2.0);
    }
}