package com.fraudops.inference;

import java.util.Map;

public interface FraudModel {
    double predictProbability(Map<String, Float> features);

    String modelVersion();

    boolean isReady();
}