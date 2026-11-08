package cn.mashangchengzhang.studentmanager;

import java.util.Calendar;
import java.util.TimeZone;
import java.util.HashSet;
import java.util.Arrays;
import java.text.SimpleDateFormat;
import java.util.Locale;

public final class ClassReminderTimeTest {
    private static Calendar date(int day, int hour, int minute) {
        Calendar result = Calendar.getInstance(TimeZone.getTimeZone("Asia/Shanghai"));
        result.clear(); result.set(2026, Calendar.OCTOBER, day, hour, minute); return result;
    }
    private static void expect(Calendar actual, Calendar expected) {
        if (actual == null || actual.getTimeInMillis() != expected.getTimeInMillis()) throw new AssertionError("Wrong scheduled class start");
    }
    private static Calendar dateTime(String value) {
        try { SimpleDateFormat format=new SimpleDateFormat("yyyy-MM-dd HH:mm",Locale.US);format.setLenient(false);format.setTimeZone(TimeZone.getTimeZone("Asia/Shanghai"));Calendar result=Calendar.getInstance(format.getTimeZone());result.setTime(format.parse(value));return result; }
        catch(Exception error){throw new AssertionError(error);}
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
        HashSet<String> none = new HashSet<>();
        expect(ClassReminderTime.nextClassStart(date(24,9,0),6,"10:00",2,"2026-10-24",none,none),date(24,10,0));
        expect(ClassReminderTime.nextClassStart(date(24,10,4),6,"10:00",2,"2026-10-24",none,none),date(24,10,0));
        expect(ClassReminderTime.nextClassStart(date(24,10,5),6,"10:00",2,"2026-10-24",none,none),dateTime("2026-11-07 10:00"));
        expect(ClassReminderTime.nextClassStart(date(31,9,0),6,"10:00",2,"2026-10-24",none,none),dateTime("2026-11-07 10:00"));
        expect(ClassReminderTime.nextClassStart(date(17,9,0),6,"10:00",2,"2026-10-24",none,none),date(24,10,0));
        expect(ClassReminderTime.nextClassStart(date(23,9,0),6,"10:00",2,"2026-10-24",new HashSet<>(Arrays.asList("2026-10-31")),new HashSet<>(Arrays.asList("2026-10-24"))),date(31,10,0));
        expect(ClassReminderTime.nextClassStart(date(24,9,0),6,"10:00",2,"2026-10-24",none,new HashSet<>(Arrays.asList("2026-10-24"))),dateTime("2026-11-07 10:00"));
        expect(ClassReminderTime.nextClassStart(dateTime("2026-12-27 09:00"),6,"10:00",2,"2026-12-26",none,none),dateTime("2027-01-09 10:00"));
        if(ClassReminderTime.regularLessonOnDate("2026-10-31",6,2,"2026-10-24"))throw new AssertionError("Rest week counted as class");
        if(!ClassReminderTime.regularLessonOnDate("2026-11-07",6,2,"2026-10-24"))throw new AssertionError("Second class skipped");
        if(ClassReminderTime.regularLessonOnDate("2026-10-17",6,2,"2026-10-24"))throw new AssertionError("Class before first anchor");
        if(ClassReminderTime.nextClassStart(date(24,9,0),6,"10:00",2,"2026-10-25",none,none)!=null)throw new AssertionError("Wrong anchor weekday accepted");
        if(ClassReminderTime.regularLessonOnDate("2026-02-30",1,2,"2026-02-16"))throw new AssertionError("Invalid cycle date accepted");
        System.out.println("Reminder regressions passed: weekly and fortnightly phases, first date, rest weeks, extra/cancelled makeup, cross-year, midnight and five-minute boundaries.");
    }
}
