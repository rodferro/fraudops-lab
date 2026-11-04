package com.fraudops.api;

import com.fraudops.service.FeatureValidationException;
import com.fraudops.inference.InferenceException;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ResponseEntity;
import org.springframework.web.ErrorResponse;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.http.converter.HttpMessageNotReadableException;

import java.time.Instant;
import java.util.List;

@RestControllerAdvice
public class ApiExceptionHandler {
    private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

    @ExceptionHandler(FeatureValidationException.class)
    public ResponseEntity<ApiError> handleFeatureValidation(FeatureValidationException exception) {
        return error(HttpStatus.BAD_REQUEST, "INVALID_FEATURES", "Request features are invalid.",
                exception.getDetails());
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiError> handleUnreadable(
            HttpMessageNotReadableException exception, HttpServletRequest request) {
        String code = request.getContentLengthLong() == 0 ? "MISSING_REQUEST_BODY" : "INVALID_JSON";
        String message = code.equals("MISSING_REQUEST_BODY")
                ? "A JSON request body is required."
                : "Request body must be valid JSON with numeric or null feature values.";
        return error(HttpStatus.BAD_REQUEST, code, message, List.of());
    }

    @ExceptionHandler(InferenceException.class)
    public ResponseEntity<ApiError> handleInferenceFailure(InferenceException exception) {
        return error(HttpStatus.INTERNAL_SERVER_ERROR, "INFERENCE_FAILED",
                "The prediction could not be computed.", List.of());
    }

    /**
     * Spring MVC client errors (unknown route, wrong method, unsupported media type, ...) carry
     * their own status. Keep it, so they are not reported or alerted on as server failures.
     */
    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiError> handleUnexpected(Exception exception) {
        if (exception instanceof ErrorResponse errorResponse) {
            HttpStatusCode status = errorResponse.getStatusCode();
            if (status.is5xxServerError()) {
                log.error("event=request_failed", exception);
            }
            HttpStatus resolved = HttpStatus.resolve(status.value());
            String code = resolved == null ? "HTTP_" + status.value() : resolved.name();
            String message = resolved == null ? "The request could not be completed." : resolved.getReasonPhrase() + ".";
            return ResponseEntity.status(status)
                    .headers(errorResponse.getHeaders())
                    .body(new ApiError(Instant.now(), status.value(), code, message, List.of()));
        }
        log.error("event=unexpected_error", exception);
        return error(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR",
                "The request could not be completed.", List.of());
    }

    private ResponseEntity<ApiError> error(
            HttpStatus status, String code, String message, List<String> details) {
        return ResponseEntity.status(status)
                .body(new ApiError(Instant.now(), status.value(), code, message, details));
    }
}
