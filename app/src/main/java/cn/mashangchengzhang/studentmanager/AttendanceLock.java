package cn.mashangchengzhang.studentmanager;

import android.app.Activity;
import android.app.ActivityManager;
import android.app.AlertDialog;
import android.app.KeyguardManager;
import android.app.admin.DevicePolicyManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.text.InputFilter;
import android.text.InputType;
import android.util.Base64;
import android.view.View;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Toast;

import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;

final class AttendanceLock {
    private static final int MIN_ANDROID = Build.VERSION_CODES.N;
    private static final String PASSWORD_KDF = "PBKDF2WithHmacSHA256";
    private static final String ANDROID_7_KDF = "PBKDF2WithHmacSHA1";
    private static final int ITERATIONS = 210_000;
    private static final int MAX_ATTEMPTS = 5;
    private static final long COOLDOWN_MS = 30_000;
    private final Activity activity;
    private final ActivityManager tasks;
    private final DevicePolicyManager policy;
    private final KeyguardManager keyguard;
    private final ComponentName admin;
    private final SharedPreferences prefs;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService passwordWorker = Executors.newSingleThreadExecutor();
    private final Consumer<Boolean> lockChanged;
    private AlertDialog dialog;
    private boolean changingLock;

    AttendanceLock(Activity activity, Consumer<Boolean> lockChanged) {
        this.activity = activity;
        this.lockChanged = lockChanged;
        tasks = activity.getSystemService(ActivityManager.class);
        policy = activity.getSystemService(DevicePolicyManager.class);
        keyguard = activity.getSystemService(KeyguardManager.class);
        admin = new ComponentName(activity, AttendanceDeviceAdmin.class);
        prefs = activity.getSharedPreferences("attendance_lock", Context.MODE_PRIVATE);
    }

    private boolean supported() {
        return Build.VERSION.SDK_INT >= MIN_ANDROID && policy != null && tasks != null && keyguard != null
            && policy.isDeviceOwnerApp(activity.getPackageName());
    }

    boolean isLocked() {
        return tasks != null && tasks.getLockTaskModeState() == ActivityManager.LOCK_TASK_MODE_LOCKED;
    }

    void restoreWhenFocused() {
        if (prefs.getBoolean("enabled", false) && !changingLock && !isLocked() && supported()
                && !keyguard.isKeyguardLocked()) {
            enable(false);
        }
    }

    boolean handleBack() {
        if (!prefs.getBoolean("enabled", false) && !isLocked() && !changingLock) return false;
        showControls();
        return true;
    }

    void showControls() {
        if (changingLock || (dialog != null && dialog.isShowing())) return;
        if (!supported()) {
            dialog = new AlertDialog.Builder(activity)
                .setTitle("签到专用模式")
                .setMessage("此功能需要 Android 7 或更新版本，并由管理员将本应用设为设备所有者。普通安装无法锁定系统的应用切换。")
                .setPositiveButton("知道了", null).show();
            return;
        }
        boolean unlock = isLocked() || prefs.getBoolean("enabled", false);
        boolean setup = !prefs.contains("hash") && !unlock;
        LinearLayout fields = new LinearLayout(activity);
        fields.setOrientation(LinearLayout.VERTICAL);
        int padding = Math.round(24 * activity.getResources().getDisplayMetrics().density);
        fields.setPadding(padding, 0, padding, 0);
        EditText input = passwordField(setup ? "设置退出密码" : "退出密码");
        fields.addView(input);
        EditText confirmation = setup ? passwordField("再次输入密码") : null;
        if (confirmation != null) fields.addView(confirmation);
        String message = unlock ? "验证密码后可返回桌面或切换应用。"
            : "锁定后，平板将停留在签到应用。点击签到页右上角“解锁”即可输入退出密码。";
        if (setup) message += "\n首次启用请设置 8 至 64 个字符的独立密码。";
        AlertDialog prompt = new AlertDialog.Builder(activity)
            .setTitle(unlock ? "解除签到专用模式" : "开启签到专用模式")
            .setMessage(message)
            .setView(fields)
            .setNegativeButton("取消", null)
            .setPositiveButton(unlock ? "解除锁定" : "开启锁定", null).create();
        dialog = prompt;
        prompt.show();
        prompt.getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        prompt.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view ->
            checkPassword(prompt, input, confirmation, unlock));
    }

    private EditText passwordField(String hint) {
        EditText field = new EditText(activity);
        field.setHint(hint);
        field.setSingleLine();
        field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        field.setFilters(new InputFilter[] {new InputFilter.LengthFilter(64)});
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            field.setImeOptions(EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING);
            field.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        }
        return field;
    }

    private void checkPassword(AlertDialog prompt, EditText input, EditText confirmation, boolean unlock) {
        if (prefs.getLong("retry_after", 0) > System.currentTimeMillis()) {
            input.setError("尝试次数过多，请稍后再试");
            return;
        }
        char[] password = copy(input);
        char[] repeated = confirmation == null ? null : copy(confirmation);
        if (repeated != null) {
            boolean matches = Arrays.equals(password, repeated);
            Arrays.fill(repeated, '\0');
            if (password.length < 8 || !matches) {
                Arrays.fill(password, '\0');
                input.setError(matches ? "请输入至少 8 个字符" : "两次输入的密码不一致");
                return;
            }
        } else if (password.length == 0) {
            input.setError("请输入退出密码");
            return;
        }
        input.getText().clear();
        if (confirmation != null) confirmation.getText().clear();
        prompt.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
        passwordWorker.execute(() -> {
            boolean valid = false;
            boolean failed = false;
            try {
                valid = confirmation != null ? savePassword(password) : matchesPassword(password);
            } catch (GeneralSecurityException | IllegalArgumentException error) {
                failed = true;
            } finally {
                Arrays.fill(password, '\0');
            }
            boolean verified = valid;
            boolean storageError = failed;
            activity.runOnUiThread(() -> {
                if (!verified && !storageError) recordFailure();
                if (!prompt.isShowing() || activity.isDestroyed()) return;
                if (!verified) {
                    input.setError(storageError ? "无法验证密码，请联系设备管理员" : "密码不正确");
                    prompt.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);
                    return;
                }
                prefs.edit().remove("failed_attempts").remove("retry_after").apply();
                prompt.dismiss();
                if (unlock) disable(); else enable(true);
            });
        });
    }

    private static char[] copy(EditText field) {
        char[] result = new char[field.length()];
        field.getText().getChars(0, result.length, result, 0);
        return result;
    }

    private boolean savePassword(char[] password) throws GeneralSecurityException {
        byte[] salt = new byte[16];
        new SecureRandom().nextBytes(salt);
        String algorithm = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O ? PASSWORD_KDF : ANDROID_7_KDF;
        if (!prefs.edit().putString("salt", Base64.encodeToString(salt, Base64.NO_WRAP))
                .putString("kdf", algorithm)
                .putString("hash", Base64.encodeToString(derive(password, salt, algorithm), Base64.NO_WRAP)).commit()) {
            throw new GeneralSecurityException("Unable to save password verifier");
        }
        return true;
    }

    private boolean matchesPassword(char[] password) throws GeneralSecurityException {
        String salt = prefs.getString("salt", null);
        String hash = prefs.getString("hash", null);
        if (salt == null || hash == null) throw new GeneralSecurityException("Missing password verifier");
        // Passwords saved by the original Android 9+ build did not store the algorithm.
        String algorithm = prefs.getString("kdf", PASSWORD_KDF);
        return MessageDigest.isEqual(derive(password, Base64.decode(salt, Base64.NO_WRAP), algorithm),
            Base64.decode(hash, Base64.NO_WRAP));
    }

    private static byte[] derive(char[] password, byte[] salt, String algorithm) throws GeneralSecurityException {
        PBEKeySpec spec = new PBEKeySpec(password, salt, ITERATIONS, 256);
        try {
            return SecretKeyFactory.getInstance(algorithm).generateSecret(spec).getEncoded();
        } finally {
            spec.clearPassword();
        }
    }

    private void recordFailure() {
        int failures = prefs.getInt("failed_attempts", 0) + 1;
        SharedPreferences.Editor edit = prefs.edit();
        if (failures >= MAX_ATTEMPTS) {
            edit.putInt("failed_attempts", 0).putLong("retry_after", System.currentTimeMillis() + COOLDOWN_MS);
        } else {
            edit.putInt("failed_attempts", failures);
        }
        edit.apply();
    }

    private void enable(boolean manual) {
        if (!supported() || keyguard.isKeyguardLocked()
                || tasks.getLockTaskModeState() != ActivityManager.LOCK_TASK_MODE_NONE) {
            if (manual) notifyUser("请先退出屏幕固定或解锁设备，再开启签到专用模式");
            return;
        }
        changingLock = true;
        try {
            policy.setLockTaskPackages(admin, new String[] {activity.getPackageName()});
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                policy.setLockTaskFeatures(admin, DevicePolicyManager.LOCK_TASK_FEATURE_NONE);
            }
            if (!policy.isLockTaskPermitted(activity.getPackageName())
                    || !prefs.edit().putBoolean("enabled", true).commit()) {
                throw new IllegalStateException("Lock configuration unavailable");
            }
            activity.startLockTask();
            handler.postDelayed(() -> {
                changingLock = false;
                if (activity.isDestroyed()) return;
                if (isLocked()) {
                    lockChanged.accept(true);
                    if (manual) notifyUser("签到专用模式已开启");
                } else {
                    if (manual) prefs.edit().putBoolean("enabled", false).apply();
                    notifyUser("系统未进入签到专用模式，请联系设备管理员");
                }
            }, 350);
        } catch (RuntimeException error) {
            changingLock = false;
            if (manual && !isLocked()) prefs.edit().putBoolean("enabled", false).apply();
            notifyUser("无法开启签到专用模式，请联系设备管理员");
        }
    }

    private void disable() {
        changingLock = true;
        try {
            if (!prefs.edit().putBoolean("enabled", false).commit()) {
                throw new IllegalStateException("Unable to save unlock state");
            }
            // A device owner can exit even if Android recreated the activity that entered lock task.
            policy.setLockTaskPackages(admin, new String[0]);
        } catch (RuntimeException error) {
            prefs.edit().putBoolean("enabled", true).apply();
            changingLock = false;
            notifyUser("无法解除锁定，请联系设备管理员");
            return;
        }
        handler.postDelayed(() -> {
            changingLock = false;
            if (isLocked()) {
                prefs.edit().putBoolean("enabled", true).apply();
                notifyUser("系统仍处于锁定状态，请重试");
            } else {
                lockChanged.accept(false);
                notifyUser("已解除签到专用模式");
            }
        }, 350);
    }

    private void notifyUser(String message) {
        Toast.makeText(activity, message, Toast.LENGTH_SHORT).show();
    }

    void close() {
        handler.removeCallbacksAndMessages(null);
        if (dialog != null) dialog.dismiss();
        passwordWorker.shutdown();
    }
}
