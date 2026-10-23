package com.fraudops.api;

import com.fraudops.inference.FraudModel;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

/** Local alert exercise only: disabled by default, fixed budget and expiry. */
@RestController
@ConditionalOnProperty(name = "fraud.observability.demo.enabled", havingValue = "true")
public class ObservabilityDemoController {
    private final FraudModel model;
    private final AtomicInteger remaining = new AtomicInteger(160);
    private final Instant expires = Instant.now().plusSeconds(180);

    public ObservabilityDemoController(FraudModel model) {
        this.model = model;
    }

    private boolean hasBudget() {
        return Instant.now().isBefore(expires) && remaining.getAndDecrement() > 0;
    }

    @PostMapping("/internal/observability/demo/failure")
    public ResponseEntity<?> failure() {
        if (!hasBudget()) return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS).build();
        // Deterministic, real inference-wrapper failure; no model/input mutation.
        return ResponseEntity.ok(model.predictProbability(Map.of()));
    }

    @PostMapping("/internal/observability/demo/latency")
    public ResponseEntity<?> latency() throws InterruptedException {
        if (!hasBudget()) return ResponseEntity.status(HttpStatus.TOO_MANY_REQUESTS).build();
        Thread.sleep(300);
        return ResponseEntity.ok(Map.of("status", "demo-completed"));
    }
}
