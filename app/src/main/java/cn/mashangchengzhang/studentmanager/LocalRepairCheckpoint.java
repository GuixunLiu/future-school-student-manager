package cn.mashangchengzhang.studentmanager;

import android.content.Context;
import android.util.AtomicFile;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;

/** Crash recovery for one explicitly confirmed repair, never a new migration on startup. */
final class LocalRepairCheckpoint {
    private final AtomicFile file;
    LocalRepairCheckpoint(Context context) { file = new AtomicFile(new File(context.getFilesDir(), "local-repair-checkpoint.json")); }
    private static String hash(String value) throws Exception {
        byte[] bytes = MessageDigest.getInstance("SHA-256").digest((value == null ? "<null>" : value).getBytes(StandardCharsets.UTF_8));
        StringBuilder text = new StringBuilder();
        for (byte b : bytes) text.append(String.format(Locale.US, "%02x", b & 255));
        return text.toString();
    }
    synchronized boolean commit(String oldStored, String candidate) {
        if (file.getBaseFile().exists()) return false;
        FileOutputStream output = null;
        try {
            JSONObject state = new JSONObject(candidate);
            if (state.optJSONArray("students") == null || state.optJSONArray("attendance") == null || state.optJSONObject("settings") == null || state.getJSONObject("settings").optJSONArray("localDataRepairs") == null) return false;
            JSONObject record = new JSONObject(); record.put("before", hash(oldStored)); record.put("after", hash(candidate)); record.put("candidate", candidate);
            output = file.startWrite(); output.write(record.toString().getBytes(StandardCharsets.UTF_8)); file.finishWrite(output); output = null;
            JSONObject saved = new JSONObject(new String(file.readFully(), StandardCharsets.UTF_8));
            boolean matches = saved.getString("before").equals(hash(oldStored)) && saved.getString("after").equals(hash(candidate)) && saved.getString("candidate").equals(candidate);
            if (!matches) file.delete();
            return matches;
        } catch (Exception ignored) { if (output != null) file.failWrite(output); file.delete(); return false; }
    }
    synchronized String recovery(String currentStored) {
        try {
            JSONObject record = new JSONObject(new String(file.readFully(), StandardCharsets.UTF_8));
            String current = hash(currentStored), candidate = record.getString("candidate");
            if (!hash(candidate).equals(record.getString("after"))) return "";
            if (current.equals(record.getString("before"))) return candidate;
            // Already durable, or superseded by a later user save. Never overwrite it.
            file.delete(); return "";
        } catch (Exception ignored) { return ""; }
    }
    synchronized void discard() { file.delete(); }
}
