package com.fraudops.api;

import com.fraudops.inference.InferenceException;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

class ApiExceptionHandlerTest {
    @Test
    void inferenceFailureIsStructuredAndDoesNotExposeInternalMessage() {
        ApiExceptionHandler handler = new ApiExceptionHandler();

        var response = handler.handleInferenceFailure(new InferenceException("native runtime details"));

        assertEquals(500, response.getStatusCode().value());
        assertEquals("INFERENCE_FAILED", response.getBody().code());
        assertFalse(response.getBody().message().contains("native runtime details"));
    }
}