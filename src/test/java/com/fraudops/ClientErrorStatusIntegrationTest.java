package com.fraudops;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.actuate.observability.AutoConfigureObservability;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;

import java.util.Collection;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Client mistakes must keep their 4xx status in responses and in http.server.requests metrics. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureObservability
class ClientErrorStatusIntegrationTest {
    @Autowired
    private TestRestTemplate http;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private MeterRegistry registry;

    @Test
    void unknownRouteWrongMethodAndWrongMediaTypeAreClientErrors() throws Exception {
        assertApiError(http.getForEntity("/nope", String.class), 404, "NOT_FOUND");

        ResponseEntity<String> wrongMethod = http.getForEntity("/api/v1/predict", String.class);
        assertApiError(wrongMethod, 405, "METHOD_NOT_ALLOWED");
        assertTrue(wrongMethod.getHeaders().getAllow().contains(HttpMethod.POST),
                "405 response should advertise the allowed method");

        HttpHeaders textPlain = new HttpHeaders();
        textPlain.setContentType(MediaType.TEXT_PLAIN);
        assertApiError(http.exchange("/api/v1/predict", HttpMethod.POST,
                new HttpEntity<>("amount=250", textPlain), String.class), 415, "UNSUPPORTED_MEDIA_TYPE");

        Set<String> recorded = awaitRecordedStatuses(Set.of("404", "405", "415"));
        assertTrue(recorded.stream().noneMatch(status -> status.startsWith("5")),
                () -> "Client errors were recorded as server errors: " + recorded);
    }

    private void assertApiError(ResponseEntity<String> response, int status, String code) throws Exception {
        assertEquals(status, response.getStatusCode().value());
        JsonNode body = objectMapper.readTree(response.getBody());
        assertEquals(status, body.path("status").asInt());
        assertEquals(code, body.path("code").asText());
        assertTrue(body.path("details").isArray());
    }

    /** Server observations are stopped after the response is written, so wait briefly for them. */
    private Set<String> awaitRecordedStatuses(Set<String> expected) throws InterruptedException {
        Set<String> statuses = Set.of();
        for (int attempt = 0; attempt < 50; attempt++) {
            Collection<Timer> timers = registry.find("http.server.requests").timers();
            statuses = timers.stream().map(timer -> timer.getId().getTag("status")).collect(Collectors.toSet());
            if (statuses.containsAll(expected)) {
                return statuses;
            }
            Thread.sleep(100);
        }
        throw new AssertionError("Expected statuses " + expected + " in http.server.requests, saw "
                + List.copyOf(statuses));
    }
}
