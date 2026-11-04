package com.fraudops.inference;

import java.util.Map;
import java.util.OptionalDouble;

public interface FraudModel {
    double predictProbability(Map<String, Float> features);

    String modelVersion();

    boolean isReady();

    /** Validation-selected review threshold recorded with the model artifact, if any. */
    OptionalDouble metadataReviewThreshold();
}
