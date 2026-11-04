package com.fraudops.inference;

import ai.onnxruntime.OnnxJavaType;
import ai.onnxruntime.NodeInfo;
import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OnnxValue;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtException;
import ai.onnxruntime.OrtSession;
import ai.onnxruntime.TensorInfo;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.FloatBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.OptionalDouble;
import java.util.Set;

@Component
public class OnnxFraudModel implements FraudModel {
    private static final String PROBABILITY_OUTPUT = "probabilities";
    public static final String REVIEW_THRESHOLD_POINTER = "/evaluation_metrics/operating_point/probability_threshold";
    private static final List<String> REQUIRED_FEATURES = List.of(
            "amount", "account_age_days", "transactions_last_24h", "distance_from_home_km",
            "device_age_days", "country_mismatch", "card_present", "channel_code",
            "merchant_category_code");

    private final ObjectMapper objectMapper;
    private final Path modelPath;
    private final Path metadataPath;
    private OrtEnvironment environment;
    private OrtSession session;
    private String version;
    private List<String> featureOrder;
    private OptionalDouble reviewThreshold = OptionalDouble.empty();

    public OnnxFraudModel(
            ObjectMapper objectMapper,
            @Value("${fraud.model.path:./fraud_model.onnx}") String modelPath,
            @Value("${fraud.model.metadata-path:./model_metadata.json}") String metadataPath) {
        this.objectMapper = objectMapper;
        this.modelPath = Path.of(modelPath).toAbsolutePath().normalize();
        this.metadataPath = Path.of(metadataPath).toAbsolutePath().normalize();
    }

    @PostConstruct
    public void initialize() {
        try {
            if (!Files.isRegularFile(modelPath) || !Files.isReadable(modelPath)) {
                throw new ModelInitializationException("ONNX model file is missing or unreadable.");
            }
            if (!Files.isRegularFile(metadataPath) || !Files.isReadable(metadataPath)) {
                throw new ModelInitializationException("Model metadata file is missing or unreadable.");
            }
            JsonNode metadata = objectMapper.readTree(metadataPath.toFile());
            version = requiredText(metadata, "model_version");
            featureOrder = readFeatureOrder(metadata);
            validateMetadataSchema(metadata);
            reviewThreshold = readReviewThreshold(metadata);

            environment = OrtEnvironment.getEnvironment();
            try (OrtSession.SessionOptions options = new OrtSession.SessionOptions()) {
                session = environment.createSession(modelPath.toString(), options);
            }
            validateSessionSchema();
        } catch (ModelInitializationException exception) {
            closeSessionAfterInitializationFailure();
            throw exception;
        } catch (IOException | OrtException | RuntimeException exception) {
            closeSessionAfterInitializationFailure();
            throw new ModelInitializationException("Could not initialize the fraud model.", exception);
        }
    }

    @Override
    public double predictProbability(Map<String, Float> features) {
        OrtSession activeSession = session;
        if (activeSession == null) {
            throw new InferenceException("Fraud model is not initialized.");
        }
        Map<String, OnnxTensor> tensors = new HashMap<>();
        try {
            for (String feature : featureOrder) {
                Float value = features.get(feature);
                if (value == null) {
                    throw new InferenceException("Validated feature map is missing a model input.");
                }
                tensors.put(feature, OnnxTensor.createTensor(
                        environment, FloatBuffer.wrap(new float[]{value}), new long[]{1, 1}));
            }
            try (OrtSession.Result result = activeSession.run(tensors)) {
                OnnxValue value = result.get(PROBABILITY_OUTPUT)
                        .orElseThrow(() -> new InferenceException("Model probability output is unavailable."));
                if (!(value instanceof OnnxTensor tensor) || !(tensor.getValue() instanceof float[][] probabilities)
                        || probabilities.length != 1 || probabilities[0].length != 2) {
                    throw new InferenceException("Model probability output has an unexpected shape.");
                }
                float probability = probabilities[0][1];
                if (!Float.isFinite(probability) || probability < 0.0f || probability > 1.0f) {
                    throw new InferenceException("Model returned an invalid probability.");
                }
                return probability;
            }
        } catch (OrtException exception) {
            throw new InferenceException("ONNX inference failed.", exception);
        } finally {
            tensors.values().forEach(OnnxTensor::close);
        }
    }

    @Override
    public String modelVersion() {
        return version;
    }

    @Override
    public boolean isReady() {
        return session != null;
    }

    @Override
    public OptionalDouble metadataReviewThreshold() {
        return reviewThreshold;
    }

    @PreDestroy
    public void close() {
        OrtSession current = session;
        session = null;
        if (current != null) {
            try {
                current.close();
            } catch (OrtException exception) {
                throw new IllegalStateException("Could not close ONNX session.", exception);
            }
        }
    }

    private void validateMetadataSchema(JsonNode metadata) {
        JsonNode inputSchema = metadata.path("input_schema");
        for (String feature : REQUIRED_FEATURES) {
            JsonNode specification = inputSchema.path(feature);
            if (!"float32".equals(specification.path("tensor_type").asText())
                    || !specification.path("nullable").asBoolean(false)) {
                throw new ModelInitializationException("Metadata input schema is incompatible.");
            }
        }
        JsonNode probability = metadata.path("output_schema").path("onnx_probability_tensor");
        if (!PROBABILITY_OUTPUT.equals(probability.path("name").asText())
                || probability.path("fraud_probability_column").asInt(-1) != 1) {
            throw new ModelInitializationException("Metadata probability output schema is incompatible.");
        }
    }

    private List<String> readFeatureOrder(JsonNode metadata) {
        JsonNode order = metadata.path("feature_order");
        if (!order.isArray()) {
            throw new ModelInitializationException("Model metadata has no feature order.");
        }
        List<String> names = new ArrayList<>();
        order.forEach(node -> names.add(node.asText()));
        if (names.size() != REQUIRED_FEATURES.size() || !new HashSet<>(names).equals(Set.copyOf(REQUIRED_FEATURES))) {
            throw new ModelInitializationException("Model metadata feature order is incompatible.");
        }
        return List.copyOf(names);
    }

    /** Optional; when present it must be a probability so a corrupt artifact fails startup. */
    private OptionalDouble readReviewThreshold(JsonNode metadata) {
        JsonNode threshold = metadata.at(REVIEW_THRESHOLD_POINTER);
        if (threshold.isMissingNode() || threshold.isNull()) {
            return OptionalDouble.empty();
        }
        double value = threshold.asDouble(Double.NaN);
        if (!threshold.isNumber() || !Double.isFinite(value) || value < 0.0 || value > 1.0) {
            throw new ModelInitializationException("Metadata review threshold must be a number between 0 and 1.");
        }
        return OptionalDouble.of(value);
    }

    private String requiredText(JsonNode node, String field) {
        String value = node.path(field).asText();
        if (value.isBlank()) {
            throw new ModelInitializationException("Model metadata is missing a required field.");
        }
        return value;
    }

    private void validateSessionSchema() throws OrtException {
        Map<String, NodeInfo> inputs = session.getInputInfo();
        if (!inputs.keySet().equals(Set.copyOf(featureOrder))) {
            throw new ModelInitializationException("ONNX model inputs do not match metadata feature order.");
        }
        for (Map.Entry<String, NodeInfo> entry : inputs.entrySet()) {
            if (!(entry.getValue().getInfo() instanceof TensorInfo tensorInfo)
                    || tensorInfo.type != OnnxJavaType.FLOAT
                    || !java.util.Arrays.equals(tensorInfo.getShape(), new long[]{-1, 1})) {
                throw new ModelInitializationException("ONNX model input tensor schema is incompatible.");
            }
        }
        Map<String, NodeInfo> outputs = session.getOutputInfo();
        NodeInfo probabilities = outputs.get(PROBABILITY_OUTPUT);
        if (probabilities == null || !(probabilities.getInfo() instanceof TensorInfo tensorInfo)
                || tensorInfo.type != OnnxJavaType.FLOAT
                || !java.util.Arrays.equals(tensorInfo.getShape(), new long[]{-1, 2})) {
            throw new ModelInitializationException("ONNX probability output tensor schema is incompatible.");
        }
    }

    private void closeSessionAfterInitializationFailure() {
        if (session != null) {
            try {
                session.close();
            } catch (OrtException ignored) {
                session = null;
                return;
            }
            session = null;
        }
    }
}