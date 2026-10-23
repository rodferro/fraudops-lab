import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;

public final class HealthCheck {
    private static final Pattern UP_STATUS = Pattern.compile("\"status\"\\s*:\\s*\"UP\"");

    private HealthCheck() {
    }

    public static void main(String[] args) {
        int port = Integer.parseInt(System.getenv().getOrDefault("SERVER_PORT", "8080"));
        URI endpoint = URI.create("http://127.0.0.1:" + port + "/actuator/health/readiness");
        try {
            HttpURLConnection connection = (HttpURLConnection) endpoint.toURL().openConnection();
            connection.setRequestMethod("GET");
            connection.setConnectTimeout(1500);
            connection.setReadTimeout(1500);
            int status = connection.getResponseCode();
            if (status != 200) {
                System.exit(1);
            }
            String response;
            try (var input = connection.getInputStream()) {
                response = new String(input.readAllBytes(), StandardCharsets.UTF_8);
            } finally {
                connection.disconnect();
            }
            if (!UP_STATUS.matcher(response).find()) {
                System.exit(1);
            }
        } catch (IOException | RuntimeException exception) {
            System.exit(1);
        }
    }
}