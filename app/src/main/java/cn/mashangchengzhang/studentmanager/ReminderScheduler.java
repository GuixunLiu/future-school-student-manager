package cn.mashangchengzhang.studentmanager;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Calendar;
import java.util.HashSet;
import java.util.Set;

final class ReminderScheduler {
    static final String ACTION_CLASS_REMINDER = "cn.mashangchengzhang.studentmanager.CLASS_REMINDER";
    static final String EXTRA_CLASS_ID = "class_id";
    static final String EXTRA_REMINDER_DATE = "reminder_date";
    private static final String PREFS_NAME = "offline_class_reminders";
    private static final String KEY_STATE = "state_json";
    private static final String KEY_SCHEDULED_IDS = "scheduled_class_ids";

    private ReminderScheduler() {}

    static void syncState(Context context, String stateJson) {
        if (stateJson == null) return;
        preferences(context).edit().putString(KEY_STATE, stateJson).apply();
        rescheduleAll(context);
    }

    static String storedState(Context context) {
        return preferences(context).getString(KEY_STATE, "");
    }

    static boolean hasEnabledReminder(String stateJson) {
        try {
            JSONArray classes = new JSONObject(stateJson).optJSONArray("classes");
            if (classes == null) return false;
            for (int index = 0; index < classes.length(); index++) {
                JSONObject course = classes.optJSONObject(index);
                if (course != null && course.optBoolean("reminderEnabled")
                    && course.optInt("scheduleWeekday") >= 1
                    && !course.optString("scheduleStart").isEmpty()) return true;
            }
        } catch (Exception ignored) {}
        return false;
    }

    static void rescheduleAll(Context context) {
        Context appContext = context.getApplicationContext();
        SharedPreferences prefs = preferences(appContext);
        Set<String> oldIds = new HashSet<>(prefs.getStringSet(KEY_SCHEDULED_IDS, new HashSet<>()));
        for (String classId : oldIds) cancelClass(appContext, classId);

        Set<String> scheduledIds = new HashSet<>();
        try {
            JSONObject state = new JSONObject(prefs.getString(KEY_STATE, "{}"));
            JSONArray classes = state.optJSONArray("classes");
            if (classes != null) {
                for (int index = 0; index < classes.length(); index++) {
                    JSONObject course = classes.optJSONObject(index);
                    if (course == null || !course.optBoolean("reminderEnabled")) continue;
                    String classId = String.valueOf(course.optLong("id"));
                    if (scheduleClass(appContext, course)) scheduledIds.add(classId);
                }
            }
        } catch (Exception ignored) {}
        prefs.edit().putStringSet(KEY_SCHEDULED_IDS, scheduledIds).apply();
    }

    static boolean scheduleClass(Context context, JSONObject course) {
        int weekday = course.optInt("scheduleWeekday");
        String startTime = course.optString("scheduleStart");
        String[] parts = startTime.split(":");
        if (weekday < 1 || weekday > 7 || parts.length != 2) return false;
        try {
            int hour = Integer.parseInt(parts[0]);
            int minute = Integer.parseInt(parts[1]);
            Calendar now = Calendar.getInstance();
            Calendar trigger = Calendar.getInstance();
            trigger.set(Calendar.SECOND, 0);
            trigger.set(Calendar.MILLISECOND, 0);
            trigger.set(Calendar.HOUR_OF_DAY, hour);
            trigger.set(Calendar.MINUTE, minute);
            trigger.add(Calendar.MINUTE, 5);
            int targetCalendarDay = weekday == 7 ? Calendar.SUNDAY : weekday + 1;
            int daysAhead = (targetCalendarDay - trigger.get(Calendar.DAY_OF_WEEK) + 7) % 7;
            trigger.add(Calendar.DAY_OF_YEAR, daysAhead);
            if (trigger.getTimeInMillis() <= now.getTimeInMillis()) trigger.add(Calendar.DAY_OF_YEAR, 7);

            String classId = String.valueOf(course.optLong("id"));
            AlarmManager alarms = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
            if (alarms == null) return false;
            alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, trigger.getTimeInMillis(), reminderIntent(context, classId));
            return true;
        } catch (Exception ignored) {
            return false;
        }
    }

    private static void cancelClass(Context context, String classId) {
        AlarmManager alarms = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (alarms != null) alarms.cancel(reminderIntent(context, classId));
    }

    private static PendingIntent reminderIntent(Context context, String classId) {
        Intent intent = new Intent(context, ClassReminderReceiver.class);
        intent.setAction(ACTION_CLASS_REMINDER);
        intent.putExtra(EXTRA_CLASS_ID, classId);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags |= PendingIntent.FLAG_IMMUTABLE;
        return PendingIntent.getBroadcast(context, requestCode(classId), intent, flags);
    }

    static int requestCode(String classId) {
        return ("future-school-class-" + classId).hashCode() & 0x7fffffff;
    }

    private static SharedPreferences preferences(Context context) {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
    }
}
