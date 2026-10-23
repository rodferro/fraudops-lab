package com.fraudops;

import com.fraudops.inference.ModelInitializationException;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FraudOpsStartupIntegrationTest {
    @Test
    void missingModelPreventsApplicationContextStartup() {
        new ApplicationContextRunner()
                .withUserConfiguration(FraudOpsApplication.class)
                .withPropertyValues(
                        "fraud.model.path=target/missing-context-model.onnx",
                        "fraud.model.metadata-path=model_metadata.json")
                .run(context -> assertModelInitializationFailure(context.getStartupFailure()));
    }

    @Test
    void invalidModelPreventsApplicationContextStartup() throws Exception {
        Path invalidModel = Files.createTempFile("fraud-invalid-context-model", ".onnx");
        try {
            Files.writeString(invalidModel, "not an ONNX model");
            new ApplicationContextRunner()
                    .withUserConfiguration(FraudOpsApplication.class)
                    .withPropertyValues(
                            "fraud.model.path=" + invalidModel.toAbsolutePath(),
                            "fraud.model.metadata-path=model_metadata.json")
                    .run(context -> assertModelInitializationFailure(context.getStartupFailure()));
        } finally {
            Files.deleteIfExists(invalidModel);
        }
    }

    private void assertModelInitializationFailure(Throwable failure) {
        assertNotNull(failure, "Application context unexpectedly started without a valid model");
        Throwable current = failure;
        while (current != null && !(current instanceof ModelInitializationException)) {
            current = current.getCause();
        }
        assertTrue(current instanceof ModelInitializationException,
                "Startup failed for a reason other than model initialization");
    }
}