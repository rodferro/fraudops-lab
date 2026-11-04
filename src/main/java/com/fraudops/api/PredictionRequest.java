package com.fraudops.api;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * All nine features must be present. An explicit JSON {@code null} is the only way to mark a value
 * as missing; absent keys, unknown keys and non-numeric values are rejected during parsing.
 */
public record PredictionRequest(
        @JsonProperty(required = true) Double amount,
        @JsonProperty(required = true) Double account_age_days,
        @JsonProperty(required = true) Double transactions_last_24h,
        @JsonProperty(required = true) Double distance_from_home_km,
        @JsonProperty(required = true) Double device_age_days,
        @JsonProperty(required = true) Double country_mismatch,
        @JsonProperty(required = true) Double card_present,
        @JsonProperty(required = true) Double channel_code,
        @JsonProperty(required = true) Double merchant_category_code) {
}
