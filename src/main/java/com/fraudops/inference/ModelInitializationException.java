package com.fraudops.inference;

public class ModelInitializationException extends RuntimeException {
    public ModelInitializationException(String message, Throwable cause) {
        super(message, cause);
    }

    public ModelInitializationException(String message) {
        super(message);
    }
}