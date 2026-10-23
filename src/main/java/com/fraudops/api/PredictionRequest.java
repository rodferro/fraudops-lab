package com.fraudops.api;

public record PredictionRequest(
        Double amount,
        Double account_age_days,
        Double transactions_last_24h,
        Double distance_from_home_km,
        Double device_age_days,
        Double country_mismatch,
        Double card_present,
        Double channel_code,
        Double merchant_category_code) {
}