package cn.mashangchengzhang.studentmanager;

import java.util.Calendar;
import java.util.Set;
import java.text.SimpleDateFormat;
import java.util.Locale;

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

    static Calendar nextClassStart(Calendar now, int weekday, String time, Set<String> extraDates, Set<String> cancelledDates) {
        if (time == null || !time.matches("\\d{2}:\\d{2}")) return null;
        int hour = Integer.parseInt(time.substring(0, 2));
        int minute = Integer.parseInt(time.substring(3, 5));
        if (hour > 23 || minute > 59) return null;
        SimpleDateFormat format = new SimpleDateFormat("yyyy-MM-dd", Locale.US);
        format.setLenient(false); format.setTimeZone(now.getTimeZone());
        Calendar best = nextClassStart(now, weekday, time);
        while (best != null && cancelledDates.contains(format.format(best.getTime()))) best.add(Calendar.DAY_OF_YEAR, 7);
        for (String date : extraDates) {
            if (date == null || !date.matches("\\d{4}-\\d{2}-\\d{2}") || cancelledDates.contains(date)) continue;
            try {
                Calendar candidate = (Calendar) now.clone();
                candidate.setTime(format.parse(date));
                candidate.set(Calendar.HOUR_OF_DAY, hour); candidate.set(Calendar.MINUTE, minute);
                candidate.set(Calendar.SECOND, 0); candidate.set(Calendar.MILLISECOND, 0);
                if (candidate.getTimeInMillis() + 5 * 60 * 1000L <= now.getTimeInMillis()) continue;
                if (best == null || candidate.before(best)) best = candidate;
            } catch (Exception ignored) {}
        }
        return best;
    }
}
