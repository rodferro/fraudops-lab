package com.fraudops.inference;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.io.InputStream;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class OnnxFraudModelParityTest {
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void matchesPythonOnnxRuntimeReferenceFixtures() throws Exception {
        JsonNode fixture;
        try (InputStream input = getClass().getResourceAsStream("/parity-cases.json")) {
            if (input == null) {
                throw new IllegalStateException("Python parity fixture is missing.");
            }
            fixture = objectMapper.readTree(input);
        }
        OnnxFraudModel model = new OnnxFraudModel(objectMapper,
                Path.of("fraud_model.onnx").toAbsolutePath().toString(),
                Path.of("model_metadata.json").toAbsolutePath().toString());
        model.initialize();
        try {
            assertEquals("1.0.0-exp000", model.modelVersion());
            double absoluteTolerance = fixture.path("absolute_tolerance").asDouble();
            double relativeTolerance = fixture.path("relative_tolerance").asDouble();
            for (JsonNode testCase : fixture.path("cases")) {
                Map<String, Float> features = readFeatures(testCase.path("features"));
                double expected = testCase.path("fraud_probability").asDouble();
                double actual = model.predictProbability(features);
                double tolerance = absoluteTolerance + relativeTolerance * Math.abs(expected);
                assertTrue(Math.abs(expected - actual) <= tolerance,
                        () -> testCase.path("name").asText() + " differed: expected " + expected + ", got " + actual);
            }
        } finally {
            model.close();
        }
    }

    private Map<String, Float> readFeatures(JsonNode features) {
        Map<String, Float> result = new HashMap<>();
        Iterator<Map.Entry<String, JsonNode>> fields = features.fields();
        while (fields.hasNext()) {
            Map.Entry<String, JsonNode> field = fields.next();
            result.put(field.getKey(), field.getValue().isNull() ? Float.NaN : field.getValue().floatValue());
        }
        return result;
    }
}