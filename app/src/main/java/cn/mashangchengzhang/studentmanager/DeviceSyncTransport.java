package cn.mashangchengzhang.studentmanager;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.text.InputType;
import android.util.Base64;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Native-only secret storage and fixed-route HTTP. No student database access. */
final class DeviceSyncTransport {
    interface Callback { void reply(String id, JSONObject result); }
    private static final String ALIAS = "future_school_device_sync_aes_v1";
    private final Activity activity;
    private final SharedPreferences prefs;
    private final Callback callback;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final AtomicBoolean busy = new AtomicBoolean(false);
    private volatile boolean closed;

    DeviceSyncTransport(Activity activity, Callback callback) {
        this.activity = activity;
        this.callback = callback;
        prefs = activity.getSharedPreferences("device_sync_encrypted_v1", Activity.MODE_PRIVATE);
    }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            generator.generateKey();
        }
        return (SecretKey) store.getKey(ALIAS, null);
    }

    private String decryptToken() throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(prefs.getString("iv", ""), Base64.NO_WRAP)));
        return new String(cipher.doFinal(Base64.decode(prefs.getString("ciphertext", ""), Base64.NO_WRAP)), StandardCharsets.UTF_8);
    }

    private void storeProfile(String endpoint, String token, String deviceId) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        String encoded = Base64.encodeToString(cipher.doFinal(token.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
        if (!prefs.edit().clear().putString("endpoint", endpoint).putString("deviceId", deviceId)
                .putString("profileId", UUID.randomUUID().toString()).putString("ciphertext", encoded)
                .putString("iv", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP)).commit()) throw new Exception("store");
    }

    static String validateEndpoint(String value, boolean allowLoopbackTest) throws Exception {
        if (value == null || value.length() > 240 || value.matches(".*[\\s\\\\].*")) throw new Exception("endpoint");
        String normalized = value.endsWith("/") ? value.substring(0, value.length()-1) : value;
        URL url = new URL(normalized);
        if (url.getUserInfo() != null || url.getQuery() != null || url.getRef() != null || url.getHost().isEmpty()
                || "appassets.androidplatform.net".equalsIgnoreCase(url.getHost()) || !url.getPath().matches("(?:/[A-Za-z0-9_-]+)*")) throw new Exception("endpoint");
        if (!"https".equals(url.getProtocol()) && !(allowLoopbackTest && "http".equals(url.getProtocol()) && "127.0.0.1".equals(url.getHost()))) throw new Exception("https");
        return normalized;
    }

    synchronized String overview() {
        JSONObject result = new JSONObject();
        try {
            result.put("configured", prefs.contains("ciphertext"));
            result.put("profileId", prefs.getString("profileId", ""));
            result.put("deviceId", prefs.getString("deviceId", ""));
            result.put("endpoint", prefs.getString("endpoint", ""));
        } catch (Exception ignored) { }
        return result.toString();
    }

    private JSONObject http(String endpoint, String token, String route, JSONObject payload) throws Exception {
        URL url = new URL(validateEndpoint(endpoint, BuildConfig.DEBUG) + route);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(7000);
            connection.setUseCaches(false);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Connection", "close");
            connection.setRequestProperty("Cookie", "");
            if (payload != null) {
                byte[] bytes = payload.toString().getBytes(StandardCharsets.UTF_8);
                if (bytes.length > 16000) throw new Exception("limit");
                connection.setRequestMethod("POST");
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setDoOutput(true);
                connection.setFixedLengthStreamingMode(bytes.length);
                try (OutputStream out = connection.getOutputStream()) { out.write(bytes); }
            }
            int status = connection.getResponseCode();
            if (status >= 300 && status < 400) return result(false, status, "服务器地址发生跳转，请核对配置", null);
            InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
            if (stream == null) return result(false, status, "服务器没有返回有效结果", null);
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            try (InputStream in = stream) {
                byte[] buffer = new byte[4096];int count;
                while ((count = in.read(buffer)) != -1) {
                    if (bytes.size() + count > 2 * 1024 * 1024) throw new Exception("limit");
                    bytes.write(buffer, 0, count);
                }
            }
            JSONObject body = new JSONObject(bytes.toString("UTF-8"));
            return result(status >= 200 && status < 300, status, status >= 400 ? "服务器未接收，请核对授权、班级和资料差异" : "", body);
        } finally { connection.disconnect(); }
    }

    private JSONObject result(boolean ok, int status, String error, JSONObject data) {
        JSONObject result = new JSONObject();
        try { result.put("ok", ok).put("status", status).put("error", error);if(data != null)result.put("data", data); } catch (Exception ignored) { }
        return result;
    }

    private void deliver(String id, JSONObject result) {
        if (!closed) activity.runOnUiThread(() -> { if (!closed) callback.reply(id, result); });
    }

    void configure() {
        activity.runOnUiThread(() -> {
            if (closed || busy.get()) return;
            LinearLayout box = new LinearLayout(activity);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(40, 10, 40, 0);
            TextView hint = new TextView(activity);hint.setText("填写管理员提供的服务器地址与设备凭证。凭证仅在安卓安全存储中保存，不进入网页或完整备份。连接后仍需核对学号、班级和课次，默认不会上传。");box.addView(hint);
            EditText endpoint = new EditText(activity);endpoint.setSingleLine(true);endpoint.setHint("HTTPS 服务器地址");endpoint.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);endpoint.setText(prefs.getString("endpoint", ""));box.addView(endpoint);
            EditText token = new EditText(activity);token.setSingleLine(true);token.setHint("设备凭证");token.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);box.addView(token);
            AlertDialog dialog = new AlertDialog.Builder(activity).setTitle("配置平板同步").setView(box).setNegativeButton("取消", (d,w) -> token.setText("")).setPositiveButton("验证连接", null).create();
            dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
                final String address, secret = token.getText().toString().trim();
                try { address = validateEndpoint(endpoint.getText().toString().trim(), BuildConfig.DEBUG);if(!secret.matches("wxd_[A-Za-z0-9_-]{43}")) throw new Exception("token"); }
                catch(Exception ignored){hint.setText("地址或设备凭证格式不正确。日常版只接受HTTPS；独立调试版才允许USB回环测试。");return;}
                if (!busy.compareAndSet(false, true)) return;
                token.setText("");dialog.dismiss();
                executor.execute(() -> {
                    try {
                        JSONObject reply = http(address, secret, "/v1/device/sync-plan", null);
                        JSONObject plan = reply.optJSONObject("data");
                        if (reply.optBoolean("ok") && plan != null && plan.optInt("protocol_version") == 1 && plan.optString("device_id").matches("[0-9a-f]{32}") && plan.optJSONObject("access") != null && plan.optJSONObject("access").optBoolean("enabled")) {
                            synchronized (this) { storeProfile(address, secret, plan.getString("device_id"));plan.put("nativeProfile", new JSONObject(overview())); }
                            deliver("configure", reply);
                        } else deliver("configure", result(false, reply.optInt("status"), "连接验证未通过，请核对设备授权及服务器版本", null));
                    } catch(Exception ignored){deliver("configure", result(false, 0, "连接失败，未启用同步。请核对网络和安全地址", null));}
                    finally {busy.set(false);}
                });
            }));dialog.show();
        });
    }

    void request(String id, String profileId, String kind, String payload) {
        if (closed || id == null || !id.matches("[A-Za-z0-9_-]{1,80}")) return;
        if (!busy.compareAndSet(false, true)) {deliver(id, result(false, 0, "正在处理另一项同步，请稍后重试", null));return;}
        executor.execute(() -> {
            try {
                final String endpoint, token;
                synchronized(this) {
                    if (!profileId.equals(prefs.getString("profileId", ""))) throw new Exception("profile");
                    endpoint = prefs.getString("endpoint", "");token = decryptToken();
                }
                if (!kind.equals("plan") && !kind.equals("event")) throw new Exception("route");
                if (payload != null && payload.getBytes(StandardCharsets.UTF_8).length > 16000) throw new Exception("limit");
                JSONObject body = kind.equals("plan") ? null : new JSONObject(payload);
                if (body != null) validateEvent(body);
                JSONObject reply = http(endpoint, token, kind.equals("plan") ? "/v1/device/sync-plan" : "/v1/device/events", body);
                if (kind.equals("plan") && reply.optBoolean("ok")) reply.getJSONObject("data").put("nativeProfile", new JSONObject(overview()));
                deliver(id, reply);
            } catch(Exception ignored){deliver(id, result(false, 0, "暂时无法同步，本机记录已保留，稍后重试", null));}
            finally {busy.set(false);}
        });
    }

    private void validateEvent(JSONObject body) throws Exception {
        if (body.length() != 2 || !body.has("kind") || !body.has("payload")) throw new Exception("fields");
        JSONObject payload = body.getJSONObject("payload");
        String allowed;
        switch (body.getString("kind")) {
            case "attendance": allowed="event_ref lesson_id student_id occurred_at consumed_units remaining_units student_revision source";break;
            case "balance": allowed="event_ref student_id occurred_at remaining_units student_revision source";break;
            case "revoke": allowed="event_ref attendance_id reason";break;
            case "leave": allowed="event_ref lesson_id student_id local_leave_ref occurred_at reason operator_label";break;
            case "cancel_leave": allowed="event_ref leave_id occurred_at reason operator_label";break;
            default: throw new Exception("kind");
        }
        java.util.Set<String> fields = new java.util.HashSet<>(java.util.Arrays.asList(allowed.split(" ")));
        java.util.Iterator<String> keys=payload.keys();
        while(keys.hasNext()) {String name=keys.next();Object value=payload.get(name);if(!fields.contains(name)||!(value==JSONObject.NULL||value instanceof Number||value instanceof String)||value instanceof String&&((String)value).length()>200)throw new Exception("fields");}
        if (!payload.optString("event_ref").matches("[A-Za-z0-9_.:-]{1,100}")) throw new Exception("reference");
    }

    void close() {closed=true;executor.shutdownNow();}
}
