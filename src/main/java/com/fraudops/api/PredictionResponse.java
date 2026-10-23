package com.fraudops.api;

public record PredictionResponse(double fraud_probability, String model_version, String decision) {
}