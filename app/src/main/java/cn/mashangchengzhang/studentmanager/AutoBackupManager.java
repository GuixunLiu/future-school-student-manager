package cn.mashangchengzhang.studentmanager;

import android.content.ContentValues;
import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;

final class AutoBackupManager {
    private static final String PREFS = "automatic_full_backups";
    private static final String KEY_LAST_DATE = "last_backup_date";
    private static final String KEY_LAST_TIME = "last_backup_time";
    private static final String KEY_LAST_FILE = "last_backup_file";
    private static final String KEY_LOCATIONS = "backup_locations";
    private static final int MAX_BACKUPS = 14;
    private static final AtomicBoolean IN_PROGRESS = new AtomicBoolean(false);

    private AutoBackupManager() {}

    static boolean shouldBackup(Context context) {
        if (IN_PROGRESS.get()) return false;
        String today = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date());
        return !today.equals(preferences(context).getString(KEY_LAST_DATE, ""));
    }

    static boolean tryStart() {
        return IN_PROGRESS.compareAndSet(false, true);
    }

    static void finish() {
        IN_PROGRESS.set(false);
    }

    static String info(Context context) {
        SharedPreferences prefs = preferences(context);
        JSONObject result = new JSONObject();
        try {
            long time = prefs.getLong(KEY_LAST_TIME, 0L);
            result.put("fileName", prefs.getString(KEY_LAST_FILE, ""));
            result.put("timestamp", time);
            result.put("timeLabel", time > 0 ? new SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.CHINA).format(new Date(time)) : "");
        } catch (Exception ignored) {}
        return result.toString();
    }

    static String backup(Context context, String stateJson, String reason) {
        JSONObject result = new JSONObject();
        boolean notifyUser = "手动备份".equals(reason);
        try {
            long now = System.currentTimeMillis();
            String stamp = new SimpleDateFormat("yyyy-MM-dd_HHmmss", Locale.US).format(new Date(now));
            String fileName = "未来学校学生管理系统_" + safeReason(reason) + "_" + stamp + ".json";
            String location = writeBackup(context, fileName, stateJson == null ? "{}" : stateJson);
            rememberAndPrune(context, location);

            SharedPreferences.Editor editor = preferences(context).edit();
            editor.putString(KEY_LAST_DATE, new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date(now)));
            editor.putLong(KEY_LAST_TIME, now);
            editor.putString(KEY_LAST_FILE, fileName);
            editor.apply();

            result.put("ok", true);
            result.put("manual", notifyUser);
            result.put("fileName", fileName);
            result.put("timeLabel", new SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.CHINA).format(new Date(now)));
        } catch (Exception error) {
            try {
                result.put("ok", false);
                result.put("manual", notifyUser);
                result.put("error", error.getMessage() == null ? "无法写入备份文件" : error.getMessage());
            } catch (Exception ignored) {}
        }
        return result.toString();
    }

    private static String writeBackup(Context context, String fileName, String stateJson) throws Exception {
        return writeBackup(context, fileName, stateJson, "自动备份");
    }

    static String verifiedRepairBackup(Context context, String stateJson, String phase) {
        JSONObject result = new JSONObject();
        try {
            if (!"before".equals(phase) && !"candidate".equals(phase)) throw new IllegalArgumentException("修复备份类型无效");
            JSONObject source = new JSONObject(stateJson);
            if (source.optJSONArray("students") == null || source.optJSONArray("attendance") == null) throw new IllegalArgumentException("备份内容无效");
            String stamp = new SimpleDateFormat("yyyy-MM-dd_HHmmss", Locale.US).format(new Date());
            String name = "未来学校_" + ("before".equals(phase) ? "资料修复前" : "资料修复结果") + "_" + stamp + "_" + UUID.randomUUID().toString().substring(0, 8) + ".json";
            byte[] expected = stateJson.getBytes(StandardCharsets.UTF_8);
            String location = writeBackup(context, name, stateJson, "资料修复备份");
            MessageDigest actualHash = MessageDigest.getInstance("SHA-256");
            long count = 0;
            try (InputStream input = location.startsWith("content://") ? context.getContentResolver().openInputStream(Uri.parse(location)) : new FileInputStream(location)) {
                if (input == null) throw new IllegalStateException("无法重新读取备份");
                byte[] buffer = new byte[32768];
                int length;
                while ((length = input.read(buffer)) != -1) { actualHash.update(buffer, 0, length); count += length; }
            }
            byte[] expectedHash = MessageDigest.getInstance("SHA-256").digest(expected);
            if (count != expected.length || !MessageDigest.isEqual(expectedHash, actualHash.digest())) throw new IllegalStateException("备份重新读取校验失败");
            StringBuilder hex = new StringBuilder();
            for (byte value : expectedHash) hex.append(String.format(Locale.US, "%02x", value & 0xff));
            result.put("ok", true); result.put("verified", true); result.put("fileName", name); result.put("bytes", count); result.put("sha256", hex.toString());
        } catch (Exception error) {
            try { result.put("ok", false); result.put("verified", false); result.put("error", "备份未完成，请检查可用空间后重试"); } catch (Exception ignored) {}
        }
        return result.toString();
    }

    private static String writeBackup(Context context, String fileName, String stateJson, String category) throws Exception {
        byte[] bytes = stateJson.getBytes(StandardCharsets.UTF_8);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ContentValues values = new ContentValues();
            values.put(MediaStore.Downloads.DISPLAY_NAME, fileName);
            values.put(MediaStore.Downloads.MIME_TYPE, "application/json");
            values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + (BuildConfig.DEBUG ? "/未来学校同步测试/" : "/未来学校学生管理系统/") + category);
            values.put(MediaStore.Downloads.IS_PENDING, 1);
            Uri uri = context.getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
            if (uri == null) throw new IllegalStateException("无法创建备份文件");
            try (OutputStream output = context.getContentResolver().openOutputStream(uri)) {
                if (output == null) throw new IllegalStateException("无法写入备份文件");
                output.write(bytes);
            } catch (Exception error) {
                context.getContentResolver().delete(uri, null, null);
                throw error;
            }
            values.clear();
            values.put(MediaStore.Downloads.IS_PENDING, 0);
            context.getContentResolver().update(uri, values, null, null);
            return uri.toString();
        }

        File base = context.getExternalFilesDir(Environment.DIRECTORY_DOCUMENTS);
        if (base == null) base = context.getFilesDir();
        File folder = new File(base, (BuildConfig.DEBUG ? "未来学校同步测试/" : "未来学校学生管理系统/") + category);
        if (!folder.exists() && !folder.mkdirs()) throw new IllegalStateException("无法创建备份目录");
        File target = new File(folder, fileName);
        try (OutputStream output = new FileOutputStream(target)) {
            output.write(bytes);
        }
        return target.getAbsolutePath();
    }

    private static void rememberAndPrune(Context context, String newLocation) {
        SharedPreferences prefs = preferences(context);
        JSONArray locations;
        try { locations = new JSONArray(prefs.getString(KEY_LOCATIONS, "[]")); }
        catch (Exception ignored) { locations = new JSONArray(); }
        locations.put(newLocation);
        while (locations.length() > MAX_BACKUPS) {
            String oldest = locations.optString(0, "");
            deleteLocation(context, oldest);
            JSONArray remaining = new JSONArray();
            for (int index = 1; index < locations.length(); index++) remaining.put(locations.optString(index));
            locations = remaining;
        }
        prefs.edit().putString(KEY_LOCATIONS, locations.toString()).apply();
    }

    private static void deleteLocation(Context context, String location) {
        try {
            if (location.startsWith("content://")) context.getContentResolver().delete(Uri.parse(location), null, null);
            else if (!location.isEmpty()) new File(location).delete();
        } catch (Exception ignored) {}
    }

    private static String safeReason(String reason) {
        String value = String.valueOf(reason == null ? "自动备份" : reason).replaceAll("[\\\\/:*?\"<>|]", "_").trim();
        return value.isEmpty() ? "自动备份" : value;
    }

    private static SharedPreferences preferences(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }
}
