package com.fraudops;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.fraudops.api.ApiExceptionHandler;
import com.fraudops.inference.InferenceException;
import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.autoconfigure.actuate.observability.AutoConfigureObservability;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = "fraud.observability.demo.enabled=true")
@AutoConfigureObservability
class ObservabilityIntegrationTest {
    @Autowired TestRestTemplate http;
    @Autowired MeterRegistry registry;

    @Test
    void recordsInferenceFailureEvenWhenTheHttpResponseIs500() {
        double before = registry.get("fraud.inference").tag("outcome", "error").counter().count();
        assertEquals(500, http.postForEntity("/internal/observability/demo/failure", null, String.class)
                .getStatusCode().value());
        assertEquals(before + 1, registry.get("fraud.inference").tag("outcome", "error").counter().count());
        assertTrue(registry.get("fraud.onnx.inference.duration").timer().count() > 0);
        assertEquals(200, http.getForEntity("/actuator/prometheus", String.class).getStatusCode().value());
    }

    @Test
    void inferenceFailureIsLoggedExactlyOnceAtErrorWithItsCause() {
        // Root logger: counts events from every logger, including the instrumented wrapper.
        Logger root = (Logger) LoggerFactory.getLogger(org.slf4j.Logger.ROOT_LOGGER_NAME);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        root.addAppender(appender);
        double errorsBefore = registry.get("fraud.inference").tag("outcome", "error").counter().count();
        try {
            assertEquals(500, http.postForEntity("/internal/observability/demo/failure", null, String.class)
                    .getStatusCode().value());
        } finally {
            root.detachAppender(appender);
        }

        List<ILoggingEvent> failures = appender.list.stream()
                .filter(event -> event.getFormattedMessage().contains("event=inference_failed"))
                .toList();
        assertEquals(1, failures.size(), () -> "Expected one event=inference_failed, got " + failures);
        ILoggingEvent failure = failures.get(0);
        assertEquals(Level.ERROR, failure.getLevel());
        assertEquals(ApiExceptionHandler.class.getName(), failure.getLoggerName());
        assertNotNull(failure.getThrowableProxy(), "the failure must be logged with its exception");
        assertEquals(InferenceException.class.getName(), failure.getThrowableProxy().getClassName());
        // The wrapper still records the failure in metrics even though it no longer logs it.
        assertEquals(errorsBefore + 1, registry.get("fraud.inference").tag("outcome", "error").counter().count());
    }
}
