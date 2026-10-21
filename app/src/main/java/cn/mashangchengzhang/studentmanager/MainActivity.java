package cn.mashangchengzhang.studentmanager;

import android.Manifest;
import android.app.Activity;
import android.app.ActivityManager;
import android.app.AlertDialog;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.JsResult;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Locale;

import org.json.JSONObject;

import org.opencv.android.OpenCVLoader;
import org.opencv.core.Mat;
import org.opencv.core.MatOfByte;
import org.opencv.core.Scalar;
import org.opencv.core.Size;
import org.opencv.dnn.Dnn;
import org.opencv.dnn.Net;
import org.opencv.imgcodecs.Imgcodecs;

public class MainActivity extends Activity {
    private static final String LOCAL_HOST = "appassets.androidplatform.net";
    private static final String APP_URL = "https://appassets.androidplatform.net/assets/index.html";
    private static final int CAMERA_REQUEST = 1201;
    private static final int FILE_CHOOSER_REQUEST = 1202;
    private static final int NOTIFICATION_REQUEST = 1203;
    private WebView webView;
    private ProgressBar progressBar;
    private PermissionRequest pendingCameraRequest;
    private ValueCallback<Uri[]> pendingFileChooser;
    private final Object sfaceLock = new Object();
    private volatile boolean openCvReady = false;
    private Net sfaceNet;
    private boolean pageReady = false;
    private boolean notificationPermissionRequested = false;
    private String pendingReminderClassId;
    private String pendingReminderDate;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.rgb(1, 101, 179));
        getWindow().setNavigationBarColor(Color.rgb(1, 79, 140));
        Bitmap taskIcon = BitmapFactory.decodeResource(getResources(), R.drawable.ic_launcher_future_school_centered_small);
        setTaskDescription(new ActivityManager.TaskDescription(
            getString(R.string.app_name), taskIcon, Color.WHITE
        ));
        try {
            openCvReady = OpenCVLoader.initLocal();
        } catch (Throwable ignored) {
            openCvReady = false;
        }

        FrameLayout root = new FrameLayout(this);
        webView = new WebView(this);
        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setProgressTintList(android.content.res.ColorStateList.valueOf(Color.rgb(245, 168, 55)));

        root.addView(webView, new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.MATCH_PARENT
        ));
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            dp(3)
        );
        root.addView(progressBar, progressParams);
        setContentView(root);

        captureReminderIntent(getIntent());
        configureWebView();
        if (savedInstanceState == null) {
            webView.loadUrl(APP_URL);
        } else {
            webView.restoreState(savedInstanceState);
        }
    }

    private void configureWebView() {
        WebView.setWebContentsDebuggingEnabled(
            (getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0
        );
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setTextZoom(100);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(false);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString() + " MaShangChengZhangAndroid/1.0");
        webView.addJavascriptInterface(new AppBridge(), "AndroidBridge");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                pageReady = true;
                deliverPendingReminder();
            }

            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return localAssetResponse(request.getUrl());
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progressBar.setProgress(newProgress);
                progressBar.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
            }

            @Override
            public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                    .setTitle(R.string.app_name)
                    .setMessage(message)
                    .setPositiveButton("知道了", (dialog, which) -> result.confirm())
                    .setOnCancelListener(dialog -> result.cancel())
                    .show();
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                    .setTitle(R.string.app_name)
                    .setMessage(message)
                    .setNegativeButton("取消", (dialog, which) -> result.cancel())
                    .setPositiveButton("确认", (dialog, which) -> result.confirm())
                    .setOnCancelListener(dialog -> result.cancel())
                    .show();
                return true;
            }

            @Override
            public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> handleWebPermission(request));
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (pendingFileChooser != null) pendingFileChooser.onReceiveValue(null);
                pendingFileChooser = callback;
                try {
                    Intent intent = params.createIntent();
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    startActivityForResult(intent, FILE_CHOOSER_REQUEST);
                    return true;
                } catch (Exception error) {
                    pendingFileChooser = null;
                    Toast.makeText(MainActivity.this, "无法打开文件选择器", Toast.LENGTH_SHORT).show();
                    return false;
                }
            }
        });
    }

    private class AppBridge {
        @JavascriptInterface
        public String getScreenOrientation() {
            int orientation = getResources().getConfiguration().orientation;
            if (orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE) return "landscape";
            if (orientation == android.content.res.Configuration.ORIENTATION_PORTRAIT) return "portrait";
            return "";
        }

        @JavascriptInterface
        public boolean hasNativeSFace() {
            return openCvReady;
        }

        @JavascriptInterface
        public String sfaceDescriptor(String base64Data) {
            return runNativeSFace(base64Data);
        }

        @JavascriptInterface
        public void saveBase64File(String fileName, String mimeType, String base64Data) {
            new Thread(() -> saveFile(fileName, mimeType, base64Data)).start();
        }

        @JavascriptInterface
        public void syncReminderState(String stateJson) {
            ReminderScheduler.syncState(MainActivity.this, stateJson);
            if (ReminderScheduler.hasEnabledReminder(stateJson)) runOnUiThread(MainActivity.this::requestNotificationPermissionIfNeeded);
        }

        @JavascriptInterface
        public boolean shouldAutoBackup() {
            return AutoBackupManager.shouldBackup(MainActivity.this);
        }

        @JavascriptInterface
        public String getAutoBackupInfo() {
            return AutoBackupManager.info(MainActivity.this);
        }

        @JavascriptInterface
        public boolean createAutoBackup(String stateJson, String reason, boolean force) {
            if (!force && !AutoBackupManager.shouldBackup(MainActivity.this)) return false;
            if (!AutoBackupManager.tryStart()) return false;
            new Thread(() -> {
                String result;
                try {
                    result = AutoBackupManager.backup(MainActivity.this, stateJson, reason);
                } finally {
                    AutoBackupManager.finish();
                }
                String script = "window.onNativeBackupCompleted && window.onNativeBackupCompleted(" + JSONObject.quote(result) + ")";
                runOnUiThread(() -> {
                    if (webView != null) webView.evaluateJavascript(script, null);
                });
            }).start();
            return true;
        }

        @JavascriptInterface
        public String createSafetyBackup(String stateJson, String reason) {
            if (!AutoBackupManager.tryStart()) {
                return "{\"ok\":false,\"error\":\"正在生成其他备份，请稍后重试\"}";
            }
            try {
                return AutoBackupManager.backup(MainActivity.this, stateJson, reason);
            } finally {
                AutoBackupManager.finish();
            }
        }
    }

    private void requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < 33 || notificationPermissionRequested) return;
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return;
        notificationPermissionRequested = true;
        requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFICATION_REQUEST);
    }

    private void captureReminderIntent(Intent intent) {
        if (intent == null) return;
        String classId = intent.getStringExtra(ReminderScheduler.EXTRA_CLASS_ID);
        if (classId == null || classId.isEmpty()) return;
        pendingReminderClassId = classId;
        pendingReminderDate = intent.getStringExtra(ReminderScheduler.EXTRA_REMINDER_DATE);
        intent.removeExtra(ReminderScheduler.EXTRA_CLASS_ID);
        intent.removeExtra(ReminderScheduler.EXTRA_REMINDER_DATE);
    }

    private void deliverPendingReminder() {
        if (!pageReady || webView == null || pendingReminderClassId == null) return;
        String script = "window.openAttendanceReminder && window.openAttendanceReminder("
            + JSONObject.quote(pendingReminderClassId) + ","
            + JSONObject.quote(pendingReminderDate == null ? "" : pendingReminderDate) + ")";
        pendingReminderClassId = null;
        pendingReminderDate = null;
        webView.evaluateJavascript(script, null);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        captureReminderIntent(intent);
        deliverPendingReminder();
    }

    private String runNativeSFace(String base64Data) {
        if (!openCvReady) return jsonError("安卓原生人脸组件未初始化");
        synchronized (sfaceLock) {
            MatOfByte encoded = null;
            Mat image = null;
            Mat blob = null;
            Mat output = null;
            Mat flat = null;
            try {
                ensureSFaceNet();
                String payload = base64Data == null ? "" : base64Data;
                int comma = payload.indexOf(',');
                if (comma >= 0) payload = payload.substring(comma + 1);
                byte[] bytes = Base64.decode(payload, Base64.DEFAULT);
                encoded = new MatOfByte(bytes);
                image = Imgcodecs.imdecode(encoded, Imgcodecs.IMREAD_COLOR);
                if (image.empty()) return jsonError("人脸图像解码失败");
                blob = Dnn.blobFromImage(image, 1.0, new Size(112, 112), new Scalar(0, 0, 0), true, false);
                sfaceNet.setInput(blob);
                output = sfaceNet.forward();
                flat = output.reshape(1, 1);
                if (flat.total() != 128) return jsonError("人脸模型输出格式异常");
                float[] values = new float[128];
                flat.get(0, 0, values);
                StringBuilder json = new StringBuilder(1900);
                json.append("{\"ok\":true,\"descriptor\":[");
                for (int index = 0; index < values.length; index++) {
                    if (!Float.isFinite(values[index])) return jsonError("人脸模型输出了无效数据");
                    if (index > 0) json.append(',');
                    json.append(String.format(Locale.US, "%.9g", values[index]));
                }
                json.append("]}");
                return json.toString();
            } catch (Throwable error) {
                return jsonError(error.getMessage() == null ? "原生模型运行失败" : error.getMessage());
            } finally {
                if (flat != null) flat.release();
                if (output != null) output.release();
                if (blob != null) blob.release();
                if (image != null) image.release();
                if (encoded != null) encoded.release();
            }
        }
    }

    private void ensureSFaceNet() throws IOException {
        if (sfaceNet != null && !sfaceNet.empty()) return;
        File model = new File(getFilesDir(), "sface_int8bq_v4.onnx");
        if (!model.exists() || model.length() < 1_000_000) {
            try (InputStream input = getAssets().open("models/sface_int8bq.onnx");
                 OutputStream output = new FileOutputStream(model)) {
                byte[] buffer = new byte[64 * 1024];
                int count;
                while ((count = input.read(buffer)) >= 0) output.write(buffer, 0, count);
            }
        }
        sfaceNet = Dnn.readNetFromONNX(model.getAbsolutePath());
        if (sfaceNet.empty()) throw new IOException("SFace 模型无法加载");
    }

    private String jsonError(String message) {
        String safe = String.valueOf(message).replace("\\", "\\\\").replace("\"", "\\\"").replace("\r", " ").replace("\n", " ");
        return "{\"ok\":false,\"error\":\"" + safe + "\"}";
    }

    private void saveFile(String fileName, String mimeType, String base64Data) {
        try {
            byte[] bytes = Base64.decode(base64Data, Base64.DEFAULT);
            String location;
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
                ContentValues values = new ContentValues();
                values.put(MediaStore.Downloads.DISPLAY_NAME, fileName);
                values.put(MediaStore.Downloads.MIME_TYPE, mimeType);
                values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/未来学校学生管理系统");
                values.put(MediaStore.Downloads.IS_PENDING, 1);
                Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                if (uri == null) throw new IOException("无法创建文件");
                try (OutputStream output = getContentResolver().openOutputStream(uri)) {
                    if (output == null) throw new IOException("无法写入文件");
                    output.write(bytes);
                }
                values.clear();
                values.put(MediaStore.Downloads.IS_PENDING, 0);
                getContentResolver().update(uri, values, null, null);
                location = "下载/未来学校学生管理系统/" + fileName;
            } else {
                File folder = new File(getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "未来学校学生管理系统");
                if (!folder.exists() && !folder.mkdirs()) throw new IOException("无法创建目录");
                File target = new File(folder, fileName);
                try (OutputStream output = new FileOutputStream(target)) { output.write(bytes); }
                location = target.getAbsolutePath();
            }
            String message = "已保存到 " + location;
            runOnUiThread(() -> Toast.makeText(MainActivity.this, message, Toast.LENGTH_LONG).show());
        } catch (Exception error) {
            runOnUiThread(() -> Toast.makeText(MainActivity.this, "文件保存失败，请重试", Toast.LENGTH_LONG).show());
        }
    }

    private WebResourceResponse localAssetResponse(Uri uri) {
        if (!LOCAL_HOST.equals(uri.getHost()) || !uri.getPath().startsWith("/assets/")) return null;
        String assetPath = uri.getPath().substring("/assets/".length());
        try {
            InputStream stream = getAssets().open(assetPath);
            String mime = mimeType(assetPath);
            String encoding = mime.startsWith("text/") || mime.contains("javascript") || mime.contains("json") ? "UTF-8" : null;
            return new WebResourceResponse(mime, encoding, stream);
        } catch (IOException ignored) {
            return null;
        }
    }

    private String mimeType(String path) {
        if (path.endsWith(".html")) return "text/html";
        if (path.endsWith(".css")) return "text/css";
        if (path.endsWith(".js")) return "application/javascript";
        if (path.endsWith(".mjs")) return "application/javascript";
        if (path.endsWith(".wasm")) return "application/wasm";
        if (path.endsWith(".onnx")) return "application/octet-stream";
        if (path.endsWith(".json")) return "application/json";
        if (path.endsWith(".png")) return "image/png";
        if (path.endsWith(".svg")) return "image/svg+xml";
        if (path.endsWith(".jpg") || path.endsWith(".jpeg")) return "image/jpeg";
        return "application/octet-stream";
    }

    private void handleWebPermission(PermissionRequest request) {
        boolean wantsCamera = false;
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)) {
                wantsCamera = true;
                break;
            }
        }
        if (!wantsCamera) {
            request.deny();
            return;
        }
        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
        } else {
            pendingCameraRequest = request;
            requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_REQUEST);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == CAMERA_REQUEST && pendingCameraRequest != null) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                pendingCameraRequest.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            } else {
                pendingCameraRequest.deny();
            }
            pendingCameraRequest = null;
        } else if (requestCode == NOTIFICATION_REQUEST && (grantResults.length == 0 || grantResults[0] != PackageManager.PERMISSION_GRANTED)) {
            Toast.makeText(this, "未开启通知权限，班级提醒已保存，但平板不会弹出系统通知", Toast.LENGTH_LONG).show();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_CHOOSER_REQUEST && pendingFileChooser != null) {
            Uri[] result = resultCode == RESULT_OK ? WebChromeClient.FileChooserParams.parseResult(resultCode, data) : null;
            pendingFileChooser.onReceiveValue(result);
            pendingFileChooser = null;
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        webView.saveState(outState);
        super.onSaveInstanceState(outState);
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        synchronized (sfaceLock) {
            sfaceNet = null;
        }
        if (webView != null) webView.destroy();
        super.onDestroy();
    }
}
