package com.fraudops.service;

import java.util.List;

public class FeatureValidationException extends RuntimeException {
    private final List<String> details;

    public FeatureValidationException(List<String> details) {
        super("Request features are invalid.");
        this.details = List.copyOf(details);
    }

    public List<String> getDetails() {
        return details;
    }
}