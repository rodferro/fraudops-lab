import com.sun.net.httpserver.HttpServer;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.Executors;

/** Experiment-only bridge: local port-forward -> client pod -> real ClusterIP. */
public final class ServiceClient {
    public static void main(String[] args) throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress(8081), 32);
        server.createContext("/", exchange -> {
            HttpURLConnection upstream = null;
            int status = 502;
            byte[] body;
            try {
                upstream = (HttpURLConnection) URI.create(
                        "http://fraudops-api:8080" + exchange.getRequestURI()).toURL().openConnection();
                upstream.setConnectTimeout(2000);
                upstream.setReadTimeout(3000);
                upstream.setRequestMethod(exchange.getRequestMethod());
                // Each request gets a fresh TCP connection for Service selection.
                upstream.setRequestProperty("Connection", "close");
                upstream.setRequestProperty("Content-Type", "application/json");
                upstream.setRequestProperty("X-EXP003-Request",
                        exchange.getRequestHeaders().getFirst("X-EXP003-Request"));
                byte[] request = exchange.getRequestBody().readAllBytes();
                if (request.length > 0) {
                    upstream.setDoOutput(true);
                    try (var output = upstream.getOutputStream()) { output.write(request); }
                }
                status = upstream.getResponseCode();
                try (var input = status >= 400 ? upstream.getErrorStream() : upstream.getInputStream()) {
                    body = input == null ? new byte[0] : input.readAllBytes();
                }
            } catch (Exception failure) {
                body = failure.toString().getBytes(StandardCharsets.UTF_8);
            } finally {
                if (upstream != null) upstream.disconnect();
            }
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, body.length);
            try (var output = exchange.getResponseBody()) { output.write(body); }
            exchange.close();
        });
        server.setExecutor(Executors.newFixedThreadPool(8));
        server.start();
        System.out.println("EXP-003 client ready on 8081; upstream fraudops-api:8080; no retries");
    }
}
