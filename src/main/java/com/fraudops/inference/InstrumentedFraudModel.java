package com.fraudops.inference;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.Map;

/** Observes the unchanged ONNX implementation without modifying its inputs/results. */
@Component
@Primary
public class InstrumentedFraudModel implements FraudModel {
    private static final Logger log = LoggerFactory.getLogger(InstrumentedFraudModel.class);
    private final OnnxFraudModel delegate;
    private final MeterRegistry registry;
    private final Tracer tracer;
    private final Counter success;
    private final Counter error;
    private final Timer duration;

    public InstrumentedFraudModel(OnnxFraudModel delegate, MeterRegistry registry,
                                  ObjectProvider<Tracer> tracer) {
        this.delegate = delegate;
        this.registry = registry;
        this.tracer = tracer.getIfAvailable(() -> Tracer.NOOP);
        // Both bounded outcomes exist before any requests, including error=0.
        this.success = Counter.builder("fraud.inference").tag("outcome", "success").register(registry);
        this.error = Counter.builder("fraud.inference").tag("outcome", "error").register(registry);
        this.duration = Timer.builder("fraud.onnx.inference.duration")
                .description("ONNX call including input tensor creation, run, and result extraction")
                .publishPercentileHistogram()
                .minimumExpectedValue(Duration.ofNanos(1_000))
                .maximumExpectedValue(Duration.ofSeconds(1))
                .register(registry);
    }

    @Override
    public double predictProbability(Map<String, Float> features) {
        Span span = tracer.nextSpan().name("onnx.inference").start();
        Timer.Sample sample = Timer.start(registry);
        try (Tracer.SpanInScope ignored = tracer.withSpan(span)) {
            double probability = delegate.predictProbability(features);
            success.increment();
            span.tag("outcome", "success");
            return probability;
        } catch (RuntimeException failure) {
            error.increment();
            span.tag("outcome", "error");
            // Deliberately exclude native exception messages and all input values.
            span.error(new InferenceException("ONNX inference failed."));
            log.warn("event=inference_failed");
            throw failure;
        } finally {
            sample.stop(duration);
            span.end();
        }
    }

    @Override
    public String modelVersion() {
        return delegate.modelVersion();
    }

    @Override
    public boolean isReady() {
        return delegate.isReady();
    }
}
