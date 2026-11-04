package com.fraudops.api;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.fraudops.inference.InferenceException;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;

import java.util.List;

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

    @Test
    void inferenceFailureIsLoggedWithItsCause() {
        Logger logger = (Logger) LoggerFactory.getLogger(ApiExceptionHandler.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        IllegalStateException cause = new IllegalStateException("native cause");
        InferenceException failure = new InferenceException("ONNX inference failed.", cause);
        try {
            new ApiExceptionHandler().handleInferenceFailure(failure);
        } finally {
            logger.detachAppender(appender);
        }

        List<ILoggingEvent> errors = appender.list.stream().filter(event -> event.getLevel() == Level.ERROR).toList();
        assertEquals(1, errors.size());
        assertEquals("event=inference_failed", errors.get(0).getFormattedMessage());
        assertEquals(InferenceException.class.getName(), errors.get(0).getThrowableProxy().getClassName());
        assertEquals(IllegalStateException.class.getName(),
                errors.get(0).getThrowableProxy().getCause().getClassName());
    }
}
