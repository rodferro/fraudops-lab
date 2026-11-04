package com.fraudops;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fraudops.inference.OnnxFraudModel;
import com.fraudops.service.PredictionService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class FraudApiIntegrationTest {
    @Autowired
    private TestRestTemplate restTemplate;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private PredictionService predictionService;

    @Test
    void startsWithModelAndReturnsPredictionAndProbeHealth() throws Exception {
        ResponseEntity<String> prediction = postPrediction(validJson());
        assertEquals(200, prediction.getStatusCode().value());
        JsonNode response = objectMapper.readTree(prediction.getBody());
        assertTrue(response.path("fraud_probability").asDouble() >= 0.0);
        assertTrue(response.path("fraud_probability").asDouble() <= 1.0);
        assertEquals("1.0.0-exp000", response.path("model_version").asText());
        assertTrue(List.of("review", "approve").contains(response.path("decision").asText()));

        assertEquals(200, restTemplate.getForEntity("/actuator/health", String.class).getStatusCode().value());
        assertEquals(200, restTemplate.getForEntity("/actuator/health/liveness", String.class).getStatusCode().value());
        assertEquals(200, restTemplate.getForEntity("/actuator/health/readiness", String.class).getStatusCode().value());
    }

    @Test
    void reviewThresholdDefaultsToModelMetadata() throws Exception {
        double metadataThreshold = objectMapper.readTree(Path.of("model_metadata.json").toFile())
                .at(OnnxFraudModel.REVIEW_THRESHOLD_POINTER).asDouble();

        assertEquals(metadataThreshold, predictionService.reviewThreshold());
        assertTrue(predictionService.reviewThresholdSource().startsWith("model_metadata.json"));
    }

    @Test
    void returnsStructuredErrorsForInvalidJsonAndFeatures() {
        ResponseEntity<String> malformed = postPrediction("{oops");
        assertEquals(400, malformed.getStatusCode().value());
        assertTrue(malformed.getBody().contains("INVALID_JSON"));

        ResponseEntity<String> wrongType = postPrediction(validJson().replace("\"amount\":250.0", "\"amount\":\"not-a-number\""));
        assertEquals(400, wrongType.getStatusCode().value());
        assertTrue(wrongType.getBody().contains("INVALID_JSON"));

        ResponseEntity<String> invalid = postPrediction(validJson().replace("\"amount\":250.0", "\"amount\":-1.0"));
        assertEquals(400, invalid.getStatusCode().value());
        assertTrue(invalid.getBody().contains("INVALID_FEATURES"));
    }

    @Test
    void rejectsMisspelledFieldInsteadOfImputingIt() throws Exception {
        // Previously "amout" was ignored and amount silently imputed, returning 200.
        assertStrictParsingError(postPrediction(validJson().replace("\"amount\"", "\"amout\"")),
                "amount is required; use null to mark a missing value");
    }

    @Test
    void rejectsUnknownExtraField() throws Exception {
        assertStrictParsingError(postPrediction(validJson().replace("{", "{\"amout\":250.0,")),
                "amout is not a recognized feature");
    }

    @Test
    void rejectsEmptyObject() throws Exception {
        assertStrictParsingError(postPrediction("{}"), "amount is required; use null to mark a missing value");
    }

    @Test
    void rejectsNumericStringInsteadOfCoercingIt() throws Exception {
        assertStrictParsingError(postPrediction(validJson().replace("\"amount\":250.0", "\"amount\":\"250\"")),
                "amount must be a JSON number or null");
    }

    @Test
    void rejectsMissingFeatureKey() throws Exception {
        assertStrictParsingError(postPrediction(validJson().replace("\"device_age_days\":30,", "")),
                "device_age_days is required; use null to mark a missing value");
    }

    @Test
    void acceptsExplicitNullAsMissingValue() throws Exception {
        ResponseEntity<String> response = postPrediction(validJson().replace("\"amount\":250.0", "\"amount\":null"));

        assertEquals(200, response.getStatusCode().value());
        double probability = objectMapper.readTree(response.getBody()).path("fraud_probability").asDouble();
        assertTrue(probability >= 0.0 && probability <= 1.0);
    }

    private void assertStrictParsingError(ResponseEntity<String> response, String expectedDetail) throws Exception {
        assertEquals(400, response.getStatusCode().value());
        JsonNode body = objectMapper.readTree(response.getBody());
        assertEquals("INVALID_JSON", body.path("code").asText());
        List<String> details = new ArrayList<>();
        body.path("details").forEach(detail -> details.add(detail.asText()));
        assertTrue(details.stream().anyMatch(detail -> detail.contains(expectedDetail)),
                () -> "Expected a detail containing '" + expectedDetail + "' but got " + details);
    }

    @Test
    void outOfBoundsFeaturesReturnFieldLevelDetails() throws Exception {
        ResponseEntity<String> response = postPrediction(validJson()
                .replace("\"account_age_days\":120", "\"account_age_days\":16777217")
                .replace("\"channel_code\":0", "\"channel_code\":-1"));

        assertEquals(400, response.getStatusCode().value());
        JsonNode body = objectMapper.readTree(response.getBody());
        assertEquals("INVALID_FEATURES", body.path("code").asText());
        List<String> details = new ArrayList<>();
        body.path("details").forEach(detail -> details.add(detail.asText()));
        assertEquals(List.of("account_age_days must be at most 16777216", "channel_code must be nonnegative"),
                details);
    }

    @Test
    void rejectsMissingRequestBodyWithStructuredError() {
        ResponseEntity<String> response = restTemplate.postForEntity("/api/v1/predict", HttpEntity.EMPTY, String.class);

        assertEquals(400, response.getStatusCode().value());
        assertTrue(response.getBody().contains("MISSING_REQUEST_BODY"));
    }

    @Test
    void handlesConcurrentInferenceRequests() throws Exception {
        try (var executor = Executors.newFixedThreadPool(12)) {
            List<Future<ResponseEntity<String>>> results = new ArrayList<>();
            for (int index = 0; index < 96; index++) {
                results.add(executor.submit(() -> postPrediction(validJson())));
            }
            for (Future<ResponseEntity<String>> result : results) {
                assertEquals(200, result.get().getStatusCode().value());
            }
        }
    }

    private ResponseEntity<String> postPrediction(String json) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        return restTemplate.exchange("/api/v1/predict", HttpMethod.POST,
                new HttpEntity<>(json, headers), String.class);
    }

    private String validJson() {
        return """
                {"amount":250.0,"account_age_days":120,"transactions_last_24h":5,
                 "distance_from_home_km":150.0,"device_age_days":30,"country_mismatch":1,
                 "card_present":0,"channel_code":0,"merchant_category_code":2}
                """;
    }
}