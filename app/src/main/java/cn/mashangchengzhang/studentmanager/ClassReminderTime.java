package cn.mashangchengzhang.studentmanager;

import java.util.Calendar;

final class ClassReminderTime {
    private ClassReminderTime() {}

    static Calendar nextClassStart(Calendar now, int weekday, String time) {
        if (weekday < 1 || weekday > 7 || time == null || !time.matches("\\d{2}:\\d{2}")) return null;
        int hour = Integer.parseInt(time.substring(0, 2));
        int minute = Integer.parseInt(time.substring(3, 5));
        if (hour > 23 || minute > 59) return null;
        int targetDay = weekday == 7 ? Calendar.SUNDAY : weekday + 1;
        Calendar start = (Calendar) now.clone();
        start.add(Calendar.DAY_OF_YEAR, -((now.get(Calendar.DAY_OF_WEEK) - targetDay + 7) % 7));
        start.set(Calendar.HOUR_OF_DAY, hour);
        start.set(Calendar.MINUTE, minute);
        start.set(Calendar.SECOND, 0);
        start.set(Calendar.MILLISECOND, 0);
        Calendar reminder = (Calendar) start.clone();
        reminder.add(Calendar.MINUTE, 5);
        if (reminder.getTimeInMillis() <= now.getTimeInMillis()) start.add(Calendar.DAY_OF_YEAR, 7);
        return start;
    }
}
