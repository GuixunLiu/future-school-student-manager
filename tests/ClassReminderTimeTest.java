package cn.mashangchengzhang.studentmanager;

import java.util.Calendar;
import java.util.TimeZone;
import java.util.HashSet;
import java.util.Arrays;

public final class ClassReminderTimeTest {
    private static Calendar date(int day, int hour, int minute) {
        Calendar result = Calendar.getInstance(TimeZone.getTimeZone("Asia/Shanghai"));
        result.clear(); result.set(2026, Calendar.OCTOBER, day, hour, minute); return result;
    }
    private static void expect(Calendar actual, Calendar expected) {
        if (actual == null || actual.getTimeInMillis() != expected.getTimeInMillis()) throw new AssertionError("Wrong scheduled class start");
    }
    public static void main(String[] args) {
        expect(ClassReminderTime.nextClassStart(date(19, 9, 0), 1, "10:00"), date(19, 10, 0));
        expect(ClassReminderTime.nextClassStart(date(19, 10, 4), 1, "10:00"), date(19, 10, 0));
        expect(ClassReminderTime.nextClassStart(date(19, 10, 5), 1, "10:00"), date(26, 10, 0));
        expect(ClassReminderTime.nextClassStart(date(20, 0, 1), 1, "23:58"), date(19, 23, 58));
        expect(ClassReminderTime.nextClassStart(date(20, 0, 4), 1, "23:58"), date(26, 23, 58));
        if (ClassReminderTime.nextClassStart(date(19, 9, 0), 1, "24:00") != null) throw new AssertionError("Invalid hour accepted");
        if (ClassReminderTime.nextClassStart(date(19, 9, 0), 1, "09:60") != null) throw new AssertionError("Invalid minute accepted");
        if (ClassReminderTime.nextClassStart(date(19, 9, 0), 0, "09:00") != null) throw new AssertionError("Invalid weekday accepted");
        HashSet<String> extras = new HashSet<>(Arrays.asList("2026-10-19", "2026-02-30"));
        HashSet<String> cancelled = new HashSet<>(Arrays.asList("2026-10-17"));
        expect(ClassReminderTime.nextClassStart(date(16, 9, 0), 6, "10:00", extras, cancelled), date(19, 10, 0));
        expect(ClassReminderTime.nextClassStart(date(19, 10, 4), 6, "10:00", extras, cancelled), date(19, 10, 0));
        expect(ClassReminderTime.nextClassStart(date(19, 10, 5), 6, "10:00", extras, cancelled), date(24, 10, 0));
        expect(ClassReminderTime.nextClassStart(date(16, 9, 0), 0, "10:00", extras, cancelled), date(19, 10, 0));
        if (ClassReminderTime.nextClassStart(date(16, 9, 0), 0, "24:00", extras, cancelled) != null) throw new AssertionError("Invalid extra lesson time accepted");
        System.out.println("Reminder schedule regressions passed: midnight, invalid times, cancelled weekends, cross-week makeup, five-minute boundary and dates-only schedules.");
    }
}
