package com.fraudops.inference;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;

class OnnxFraudModelStartupTest {
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final Path metadata = Path.of("model_metadata.json").toAbsolutePath();

    @Test
    void missingModelPreventsModelInitialization() {
        OnnxFraudModel model = new OnnxFraudModel(objectMapper,
                Path.of("target", "missing-model.onnx").toAbsolutePath().toString(), metadata.toString());

        assertThrows(ModelInitializationException.class, model::initialize);
        assertFalse(model.isReady());
    }

    @Test
    void readsReviewThresholdFromMetadata() throws Exception {
        OnnxFraudModel model = new OnnxFraudModel(objectMapper,
                Path.of("fraud_model.onnx").toAbsolutePath().toString(), metadata.toString());
        model.initialize();
        try {
            double expected = objectMapper.readTree(metadata.toFile())
                    .at(OnnxFraudModel.REVIEW_THRESHOLD_POINTER).asDouble();
            assertEquals(expected, model.metadataReviewThreshold().orElseThrow());
        } finally {
            model.close();
        }
    }

    @Test
    void invalidMetadataThresholdPreventsModelInitialization() throws Exception {
        ObjectNode corrupt = (ObjectNode) objectMapper.readTree(metadata.toFile());
        ((ObjectNode) corrupt.at("/evaluation_metrics/operating_point")).put("probability_threshold", 1.5);
        Path corruptMetadata = Files.createTempFile("fraud-metadata-bad-threshold", ".json");
        try {
            objectMapper.writeValue(corruptMetadata.toFile(), corrupt);
            OnnxFraudModel model = new OnnxFraudModel(objectMapper,
                    Path.of("fraud_model.onnx").toAbsolutePath().toString(), corruptMetadata.toString());

            assertThrows(ModelInitializationException.class, model::initialize);
            assertFalse(model.isReady());
        } finally {
            Files.deleteIfExists(corruptMetadata);
        }
    }

    @Test
    void invalidModelPreventsModelInitialization() throws Exception {
        Path invalidModel = Files.createTempFile("fraud-invalid-model", ".onnx");
        try {
            Files.writeString(invalidModel, "not an ONNX model");
            OnnxFraudModel model = new OnnxFraudModel(objectMapper,
                    invalidModel.toAbsolutePath().toString(), metadata.toString());

            assertThrows(ModelInitializationException.class, model::initialize);
            assertFalse(model.isReady());
        } finally {
            Files.deleteIfExists(invalidModel);
        }
    }
}