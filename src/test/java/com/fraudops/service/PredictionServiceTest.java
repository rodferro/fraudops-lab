package com.fraudops.service;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.fraudops.api.PredictionRequest;
import com.fraudops.api.PredictionResponse;
import com.fraudops.inference.FraudModel;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;

import java.util.List;
import java.util.Map;
import java.util.OptionalDouble;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PredictionServiceTest {
    private AtomicReference<Map<String, Float>> capturedFeatures;
    private double modelProbability;
    private PredictionService service;

    private static final double METADATA_THRESHOLD = 0.06729906797409058;
    private OptionalDouble metadataThreshold;
    private FraudModel model;

    @BeforeEach
    void setUp() {
        capturedFeatures = new AtomicReference<>();
        modelProbability = METADATA_THRESHOLD;
        metadataThreshold = OptionalDouble.of(METADATA_THRESHOLD);
        model = new FraudModel() {
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

            @Override
            public OptionalDouble metadataReviewThreshold() {
                return metadataThreshold;
            }
        };
        service = new PredictionService(model, "");
    }

    @Test
    void warnsWhenOverrideDiffersFromMetadataThreshold() {
        List<ILoggingEvent> events = captureServiceLogs(() -> new PredictionService(model, "0.5"));

        List<ILoggingEvent> warnings = events.stream()
                .filter(event -> event.getLevel() == Level.WARN)
                .toList();
        assertEquals(1, warnings.size());
        assertEquals("event=review_threshold_override_differs override=0.5 metadata=" + METADATA_THRESHOLD
                + " model_version=1.0.0-exp000", warnings.get(0).getFormattedMessage());
    }

    @Test
    void doesNotWarnWhenOverrideEqualsMetadataThreshold() {
        List<ILoggingEvent> events = captureServiceLogs(
                () -> new PredictionService(model, Double.toString(METADATA_THRESHOLD)));

        assertTrue(events.stream().noneMatch(event -> event.getLevel() == Level.WARN));
        assertTrue(events.stream().anyMatch(event ->
                event.getFormattedMessage().startsWith("event=review_threshold_configured")));
    }

    @Test
    void doesNotWarnWithoutOverrideOrWithoutMetadataThreshold() {
        List<ILoggingEvent> metadataOnly = captureServiceLogs(() -> new PredictionService(model, ""));
        metadataThreshold = OptionalDouble.empty();
        List<ILoggingEvent> overrideOnly = captureServiceLogs(() -> new PredictionService(model, "0.5"));

        assertTrue(metadataOnly.stream().noneMatch(event -> event.getLevel() == Level.WARN));
        assertTrue(overrideOnly.stream().noneMatch(event -> event.getLevel() == Level.WARN));
    }

    private static List<ILoggingEvent> captureServiceLogs(Runnable action) {
        Logger logger = (Logger) LoggerFactory.getLogger(PredictionService.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            action.run();
        } finally {
            logger.detachAppender(appender);
        }
        return List.copyOf(appender.list);
    }

    @Test
    void metadataThresholdIsTheDefault() {
        assertEquals(METADATA_THRESHOLD, service.reviewThreshold());
        assertEquals(PredictionService.METADATA_SOURCE, service.reviewThresholdSource());
    }

    @Test
    void configuredThresholdOverridesMetadata() {
        PredictionService overridden = new PredictionService(model, " 0.5 ");

        assertEquals(0.5, overridden.reviewThreshold());
        assertEquals(PredictionService.OVERRIDE_SOURCE, overridden.reviewThresholdSource());
        modelProbability = 0.4;
        assertEquals("approve", overridden.predict(validRequest()).decision());
    }

    @Test
    void invalidOverrideFailsStartup() {
        assertThrows(IllegalArgumentException.class, () -> new PredictionService(model, "not-a-number"));
        assertThrows(IllegalArgumentException.class, () -> new PredictionService(model, "1.5"));
        assertThrows(IllegalArgumentException.class, () -> new PredictionService(model, "NaN"));
    }

    @Test
    void missingMetadataThresholdWithoutOverrideFailsStartup() {
        metadataThreshold = OptionalDouble.empty();

        assertThrows(IllegalStateException.class, () -> new PredictionService(model, ""));
        assertEquals(0.2, new PredictionService(model, "0.2").reviewThreshold());
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

    @Test
    void rejectsNegativeCategoryCodesWithFieldDetails() {
        PredictionRequest invalid = new PredictionRequest(250.0, 120.0, 5.0, 150.0, 30.0,
                1.0, 0.0, -1.0, -3.0);

        FeatureValidationException exception = assertThrows(
                FeatureValidationException.class, () -> service.predict(invalid));

        assertEquals(List.of("channel_code must be nonnegative", "merchant_category_code must be nonnegative"),
                exception.getDetails());
    }

    @Test
    void rejectsValuesAboveExactFloat32IntegerRange() {
        PredictionRequest invalid = new PredictionRequest(16_777_217.0, 16_777_217.0, 5.0, 150.0, 30.0,
                1.0, 0.0, 0.0, 2.0);

        FeatureValidationException exception = assertThrows(
                FeatureValidationException.class, () -> service.predict(invalid));

        assertEquals(List.of("amount must be at most 16777216", "account_age_days must be at most 16777216"),
                exception.getDetails());
    }

    @Test
    void acceptsBoundaryValues() {
        PredictionRequest boundary = new PredictionRequest(16_777_216.0, 16_777_216.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 16_777_216.0, 0.0);

        service.predict(boundary);

        assertEquals(16_777_216.0f, capturedFeatures.get().get("account_age_days"));
        assertEquals(16_777_216.0f, capturedFeatures.get().get("channel_code"));
    }

    private PredictionRequest validRequest() {
        return new PredictionRequest(250.0, 120.0, 5.0, 150.0, 30.0,
                1.0, 0.0, 0.0, 2.0);
    }
}