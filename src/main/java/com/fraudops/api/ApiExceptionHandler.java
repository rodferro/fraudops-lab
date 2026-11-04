package com.fraudops.api;

import com.fasterxml.jackson.databind.JsonMappingException;
import com.fasterxml.jackson.databind.exc.MismatchedInputException;
import com.fasterxml.jackson.databind.exc.UnrecognizedPropertyException;
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
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@RestControllerAdvice
public class ApiExceptionHandler {
    private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);
    // RequestResponseBodyMethodProcessor's message when a required body has no content.
    private static final String SPRING_MISSING_BODY_MESSAGE = "Required request body is missing";
    // Jackson reports absent required creator properties without a field path.
    private static final Pattern MISSING_CREATOR_PROPERTY =
            Pattern.compile("Missing required creator property '([A-Za-z0-9_]+)'");

    @ExceptionHandler(FeatureValidationException.class)
    public ResponseEntity<ApiError> handleFeatureValidation(FeatureValidationException exception) {
        return error(HttpStatus.BAD_REQUEST, "INVALID_FEATURES", "Request features are invalid.",
                exception.getDetails());
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiError> handleUnreadable(
            HttpMessageNotReadableException exception, HttpServletRequest request) {
        if (isMissingBody(exception, request)) {
            return error(HttpStatus.BAD_REQUEST, "MISSING_REQUEST_BODY", "A JSON request body is required.",
                    List.of());
        }
        return error(HttpStatus.BAD_REQUEST, "INVALID_JSON",
                "Request body must be a JSON object with all nine features as numbers or null.",
                unreadableDetails(exception));
    }

    /**
     * Content-Length: 0, or a body of unknown length (chunked) that Spring found empty. For a
     * required {@code @RequestBody}, Spring peeks the stream before deserializing and reports an
     * empty body as a cause-less exception with this message; parse failures always carry a cause.
     * This handler never reads the request stream itself.
     */
    private static boolean isMissingBody(HttpMessageNotReadableException exception, HttpServletRequest request) {
        long contentLength = request.getContentLengthLong();
        if (contentLength == 0) {
            return true;
        }
        return contentLength < 0
                && exception.getCause() == null
                && String.valueOf(exception.getMessage()).startsWith(SPRING_MISSING_BODY_MESSAGE);
    }

    /** Field-level reasons for strict-contract failures; never echoes request values. */
    private static List<String> unreadableDetails(HttpMessageNotReadableException exception) {
        if (exception.getCause() instanceof UnrecognizedPropertyException unknown) {
            return List.of(unknown.getPropertyName() + " is not a recognized feature");
        }
        if (exception.getCause() instanceof MismatchedInputException mismatch) {
            // A misspelled key surfaces here as the missing correct key: required-property
            // checks run before buffered unknown properties are reported.
            Matcher missing = MISSING_CREATOR_PROPERTY.matcher(String.valueOf(mismatch.getOriginalMessage()));
            if (missing.find()) {
                return List.of(missing.group(1) + " is required; use null to mark a missing value");
            }
            String field = mismatch.getPath().stream()
                    .map(JsonMappingException.Reference::getFieldName)
                    .filter(name -> name != null)
                    .reduce((first, second) -> second)
                    .orElse(null);
            if (field != null) {
                return List.of(field + " must be a JSON number or null");
            }
            return List.of("request body must be a JSON object containing all nine features");
        }
        return List.of();
    }

    @ExceptionHandler(InferenceException.class)
    public ResponseEntity<ApiError> handleInferenceFailure(InferenceException exception) {
        // InferenceException messages are constants and its only cause is OrtException, so this
        // records the cause and stack trace without request feature values.
        log.error("event=inference_failed", exception);
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
