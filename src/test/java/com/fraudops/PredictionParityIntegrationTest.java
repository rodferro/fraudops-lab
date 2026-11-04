package com.fraudops;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * End-to-end parity: every Python fixture goes through JSON parsing, request validation, the
 * named feature mapping, ONNX inference and the decision policy, then is compared with both the
 * Python ONNX Runtime and scikit-learn references. A feature-mapping bug in PredictionService
 * fails here even though the model-level parity test would still pass.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class PredictionParityIntegrationTest {
    @Autowired
    private TestRestTemplate http;

    @Autowired
    private ObjectMapper objectMapper;

    @Test
    void everyFixtureMatchesPythonReferencesThroughTheHttpApi() throws Exception {
        JsonNode fixture = readFixture();
        assertEquals(sha256(Path.of("fraud_model.onnx")), fixture.path("model_sha256").asText(),
                "parity-cases.json was generated for a different fraud_model.onnx; rerun generate_parity_fixtures.py");
        double absoluteTolerance = fixture.path("absolute_tolerance").asDouble();
        double relativeTolerance = fixture.path("relative_tolerance").asDouble();

        List<String> failures = new ArrayList<>();
        for (JsonNode testCase : fixture.path("cases")) {
            String name = testCase.path("name").asText();
            ResponseEntity<String> response = post(objectMapper.writeValueAsString(testCase.path("features")));
            if (response.getStatusCode().value() != 200) {
                failures.add(name + ": HTTP " + response.getStatusCode().value() + " " + response.getBody());
                continue;
            }
            JsonNode body = objectMapper.readTree(response.getBody());
            double actual = body.path("fraud_probability").asDouble();
            for (String reference : List.of("fraud_probability", "sklearn_fraud_probability")) {
                double expected = testCase.path(reference).asDouble();
                if (Math.abs(expected - actual) > absoluteTolerance + relativeTolerance * Math.abs(expected)) {
                    failures.add(name + ": probability " + actual + " differs from " + reference + " " + expected);
                }
            }
            String expectedDecision = testCase.path("expected_decision").asText();
            if (!expectedDecision.equals(body.path("decision").asText())) {
                failures.add(name + ": decision " + body.path("decision").asText() + ", expected " + expectedDecision);
            }
            if (!fixture.path("model_version").asText().equals(body.path("model_version").asText())) {
                failures.add(name + ": model_version " + body.path("model_version").asText());
            }
        }
        assertTrue(fixture.path("cases").size() >= 6, "fixture should contain every parity case");
        assertTrue(failures.isEmpty(), () -> "End-to-end parity failures:\n" + String.join("\n", failures));
    }

    private JsonNode readFixture() throws Exception {
        try (InputStream input = getClass().getResourceAsStream("/parity-cases.json")) {
            if (input == null) {
                throw new IllegalStateException("Python parity fixture is missing.");
            }
            return objectMapper.readTree(input);
        }
    }

    private ResponseEntity<String> post(String json) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        return http.exchange("/api/v1/predict", HttpMethod.POST, new HttpEntity<>(json, headers), String.class);
    }

    private static String sha256(Path path) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path)));
    }
}
