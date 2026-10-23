package com.fraudops;

import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.autoconfigure.actuate.observability.AutoConfigureObservability;

import static org.junit.jupiter.api.Assertions.assertEquals;
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
}
