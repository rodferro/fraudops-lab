package com.fraudops.api;

import com.fraudops.service.PredictionService;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1")
public class PredictionController {
    private static final Logger log = LoggerFactory.getLogger(PredictionController.class);
    private final PredictionService predictionService;
    private final Counter approve;
    private final Counter review;

    public PredictionController(PredictionService predictionService, MeterRegistry registry) {
        this.predictionService = predictionService;
        this.approve = Counter.builder("fraud.decisions").tag("decision", "approve").register(registry);
        this.review = Counter.builder("fraud.decisions").tag("decision", "review").register(registry);
    }

    @PostMapping("/predict")
    public PredictionResponse predict(@RequestBody PredictionRequest request) {
        PredictionResponse response = predictionService.predict(request);
        (response.decision().equals("review") ? review : approve).increment();
        log.info("event=prediction_completed decision={} model_version={}",
                response.decision(), response.model_version());
        return response;
    }
}
