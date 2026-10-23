package com.fraudops.inference;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;

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