package com.fraudops.config;

import com.fraudops.inference.FraudModel;
import org.springframework.boot.actuate.health.Health;
import org.springframework.boot.actuate.health.HealthIndicator;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class ModelConfiguration {
    @Bean
    HealthIndicator modelHealthIndicator(FraudModel fraudModel) {
        return () -> fraudModel.isReady()
                ? Health.up().withDetail("modelVersion", fraudModel.modelVersion()).build()
                : Health.down().withDetail("model", "not initialized").build();
    }
}