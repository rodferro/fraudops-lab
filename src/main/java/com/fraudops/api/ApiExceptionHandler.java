package com.fraudops.api;

import com.fraudops.service.FeatureValidationException;
import com.fraudops.inference.InferenceException;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.http.converter.HttpMessageNotReadableException;

import java.time.Instant;
import java.util.List;

@RestControllerAdvice
public class ApiExceptionHandler {
    @ExceptionHandler(FeatureValidationException.class)
    public org.springframework.http.ResponseEntity<ApiError> handleFeatureValidation(
            FeatureValidationException exception) {
        return error(HttpStatus.BAD_REQUEST, "INVALID_FEATURES", "Request features are invalid.",
                exception.getDetails());
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public org.springframework.http.ResponseEntity<ApiError> handleUnreadable(
            HttpMessageNotReadableException exception, HttpServletRequest request) {
        String code = request.getContentLengthLong() == 0 ? "MISSING_REQUEST_BODY" : "INVALID_JSON";
        String message = code.equals("MISSING_REQUEST_BODY")
                ? "A JSON request body is required."
                : "Request body must be valid JSON with numeric or null feature values.";
        return error(HttpStatus.BAD_REQUEST, code, message, List.of());
    }

        @ExceptionHandler(InferenceException.class)
        public org.springframework.http.ResponseEntity<ApiError> handleInferenceFailure(InferenceException exception) {
                return error(HttpStatus.INTERNAL_SERVER_ERROR, "INFERENCE_FAILED",
                                "The prediction could not be computed.", List.of());
        }

    @ExceptionHandler(Exception.class)
    public org.springframework.http.ResponseEntity<ApiError> handleUnexpected(Exception exception) {
        return error(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR",
                "The request could not be completed.", List.of());
    }

    private org.springframework.http.ResponseEntity<ApiError> error(
            HttpStatus status, String code, String message, List<String> details) {
        return org.springframework.http.ResponseEntity.status(status)
                .body(new ApiError(Instant.now(), status.value(), code, message, details));
    }
}