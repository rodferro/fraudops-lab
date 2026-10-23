package com.fraudops.inference;

public class InferenceException extends RuntimeException {
    public InferenceException(String message, Throwable cause) {
        super(message, cause);
    }

    public InferenceException(String message) {
        super(message);
    }
}