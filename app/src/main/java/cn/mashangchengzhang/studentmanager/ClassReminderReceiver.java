package cn.mashangchengzhang.studentmanager;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.service.notification.StatusBarNotification;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

public class ClassReminderReceiver extends BroadcastReceiver {
    private static final String CHANNEL_ID = "class_absence_reminders_v1";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (!ReminderScheduler.ACTION_CLASS_REMINDER.equals(intent.getAction())) return;
        String classId = intent.getStringExtra(ReminderScheduler.EXTRA_CLASS_ID);
        try {
            JSONObject state = new JSONObject(ReminderScheduler.storedState(context));
            JSONObject course = findClass(state.optJSONArray("classes"), classId);
            if (course == null || !course.optBoolean("reminderEnabled")) return;
            boolean silentRefresh = intent.getBooleanExtra("leave_silent_refresh", false);
            if (!silentRefresh) ReminderScheduler.scheduleClass(context, course);

            String today = intent.getStringExtra(ReminderScheduler.EXTRA_REMINDER_DATE);
            if (today == null || !today.matches("\\d{4}-\\d{2}-\\d{2}")) {
                today = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date());
            }
            if (!isClassDate(course, today)) return;
            List<String> absentNames = absentStudents(state, course, today);
            NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
            if (manager == null) return;
            int notificationId = ReminderScheduler.requestCode(classId);
            if (absentNames.isEmpty()) {
                manager.cancel(notificationId);
                return;
            }
            if (Build.VERSION.SDK_INT >= 33 && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;

            ensureChannel(manager);
            String className = course.optString("name", "班级");
            String teacher = course.optString("teacher", "");
            String classroom = course.optString("classroom", "");
            String names = joinNames(absentNames);
            String title = className + "有 " + absentNames.size() + " 名学生未签到";
            String detail = names + (teacher.isEmpty() ? "" : " · " + teacher) + (classroom.isEmpty() ? "" : " · " + classroom);

            Intent openIntent = new Intent(context, FutureSchoolLauncherActivity.class);
            openIntent.setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            openIntent.putExtra(ReminderScheduler.EXTRA_CLASS_ID, classId);
            openIntent.putExtra(ReminderScheduler.EXTRA_REMINDER_DATE, today);
            int pendingFlags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) pendingFlags |= PendingIntent.FLAG_IMMUTABLE;
            PendingIntent contentIntent = PendingIntent.getActivity(context, notificationId, openIntent, pendingFlags);

            Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(context, CHANNEL_ID)
                : new Notification.Builder(context);
            builder.setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(title)
                .setContentText(detail)
                .setStyle(new Notification.BigTextStyle().bigText(detail + "。点击查看最新未签到名单。"))
                .setContentIntent(contentIntent)
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_REMINDER)
                .setPriority(Notification.PRIORITY_HIGH)
                .setOnlyAlertOnce(silentRefresh)
                .setWhen(System.currentTimeMillis())
                .setShowWhen(true);
            if (!silentRefresh) builder.setDefaults(Notification.DEFAULT_SOUND | Notification.DEFAULT_VIBRATE);
            manager.notify(notificationId, builder.build());
        } catch (Exception ignored) {}
    }

    private static boolean isClassDate(JSONObject course, String date) {
        return !ReminderScheduler.dateSet(course.optJSONArray("cancelledLessonDates")).contains(date)
            && (ReminderScheduler.dateSet(course.optJSONArray("extraLessonDates")).contains(date)
                || ClassReminderTime.regularLessonOnDate(date, course.optInt("scheduleWeekday"), course.optInt("scheduleIntervalWeeks", 1), course.optString("scheduleAnchorDate")));
    }

    // Update only notifications already visible; a local edit must not trigger a new alarm.
    static void refreshVisibleNotifications(Context context) {
        try {
            NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
            if (manager == null) return;
            JSONObject state = new JSONObject(ReminderScheduler.storedState(context));
            JSONArray classes = state.optJSONArray("classes");
            if (classes == null) return;
            String today = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date());
            for (StatusBarNotification posted : manager.getActiveNotifications()) {
                for (int index = 0; index < classes.length(); index++) {
                    JSONObject course = classes.optJSONObject(index);
                    if (course == null) continue;
                    String id = String.valueOf(course.optLong("id"));
                    if (posted.getId() != ReminderScheduler.requestCode(id)) continue;
                    String postedDate = new SimpleDateFormat("yyyy-MM-dd", Locale.US).format(new Date(posted.getPostTime()));
                    if (!today.equals(postedDate) || !course.optBoolean("reminderEnabled") || !isClassDate(course, today)) manager.cancel(posted.getId());
                    else {
                        Intent refresh = new Intent(ReminderScheduler.ACTION_CLASS_REMINDER);
                        refresh.putExtra(ReminderScheduler.EXTRA_CLASS_ID, id);
                        refresh.putExtra(ReminderScheduler.EXTRA_REMINDER_DATE, today);
                        refresh.putExtra("leave_silent_refresh", true);
                        new ClassReminderReceiver().onReceive(context, refresh);
                    }
                    break;
                }
            }
        } catch (Exception ignored) {}
    }

    private JSONObject findClass(JSONArray classes, String classId) {
        if (classes == null) return null;
        for (int index = 0; index < classes.length(); index++) {
            JSONObject course = classes.optJSONObject(index);
            if (course != null && String.valueOf(course.optLong("id")).equals(classId)) return course;
        }
        return null;
    }

    private List<String> absentStudents(JSONObject state, JSONObject course, String date) {
        String className = course.optString("name");
        boolean partialLesson = ReminderScheduler.dateSet(course.optJSONArray("partialLessonDates")).contains(date);
        Set<String> checkedIds = new HashSet<>();
        JSONArray attendance = state.optJSONArray("attendance");
        if (attendance != null) {
            for (int index = 0; index < attendance.length(); index++) {
                JSONObject record = attendance.optJSONObject(index);
                if (record != null && date.equals(record.optString("date"))) checkedIds.add(String.valueOf(record.optLong("studentId")));
            }
        }
        List<String> absent = new ArrayList<>();
        JSONArray students = state.optJSONArray("students");
        if (students == null) return absent;
        for (int index = 0; index < students.length(); index++) {
            JSONObject student = students.optJSONObject(index);
            if (student == null || student.optBoolean("longTermAbsent")) continue;
            if (!className.trim().equals(student.optString("className").trim())) continue;
            if (ReminderScheduler.dateSet(student.optJSONArray("leaveDates")).contains(date)) continue;
            if (ReminderScheduler.dateSet(student.optJSONArray("cancelledLessonDates")).contains(date)) continue;
            if (partialLesson && !ReminderScheduler.dateSet(student.optJSONArray("extraLessonDates")).contains(date)) continue;
            if (!checkedIds.contains(String.valueOf(student.optLong("id")))) absent.add(student.optString("name", "未命名学生"));
        }
        return absent;
    }

    private String joinNames(List<String> names) {
        int visible = Math.min(names.size(), 6);
        StringBuilder result = new StringBuilder();
        for (int index = 0; index < visible; index++) {
            if (index > 0) result.append("、");
            result.append(names.get(index));
        }
        if (names.size() > visible) result.append("等");
        return result.toString();
    }

    private void ensureChannel(NotificationManager manager) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "开课未签到提醒", NotificationManager.IMPORTANCE_HIGH);
        channel.setDescription("开课 5 分钟后提醒仍未签到的学生");
        channel.enableVibration(true);
        manager.createNotificationChannel(channel);
    }
}
